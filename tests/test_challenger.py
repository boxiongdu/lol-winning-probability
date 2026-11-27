"""Challenger sampling tests use synthetic API-shaped responses, never real keys."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from src.errors import ConfigurationError, InputError
from src.ingestion.client import RiotClient, RiotError
from src.ingestion.challenger import collect_challenger, shuffled_pool, detail_reason, roster_players
from src.ingestion.audit import champion_audit


class FakeClient:
    region = "americas"
    retry_count = 0

    def __init__(self, payloads, size=6, players=2, fail_detail_at=None, fail_timeline=False):
        self.payloads = payloads
        self.ids = [f"NA1_{i}" for i in range(1, size+1)]
        self.puuids = ["synthetic-player-1", "synthetic-player-6"][:players]
        self.requests = 0
        self.calls = []
        self.detail_count = 0
        self.fail_detail_at = fail_detail_at
        self.fail_timeline = fail_timeline

    def challenger_roster(self, platform):
        self.calls.append(("roster", platform))
        self.requests += 1
        return dict(tier="CHALLENGER", queue="RANKED_SOLO_5x5", entries=[dict(puuid=p) for p in self.puuids])

    def history_pool(self, puuid, **kwargs):
        self.calls.append(("history", puuid))
        self.requests += 1
        return list(reversed(self.ids))

    def match(self, match_id):
        self.calls.append(("match", match_id))
        self.requests += 1
        self.detail_count += 1
        if self.detail_count == self.fail_detail_at:
            raise RiotError("temporary failure", 503)
        result = deepcopy(self.payloads[0])
        result["metadata"]["matchId"] = match_id
        return result

    def timeline(self, match_id):
        self.calls.append(("timeline", match_id))
        self.requests += 1
        if self.fail_timeline:
            raise RiotError("timeline unavailable", 404)
        result = deepcopy(self.payloads[1])
        result["metadata"]["matchId"] = match_id
        return result


def options(tmp_path, payloads):
    stamp = payloads[0]["info"]["gameStartTimestamp"] // 1000
    return dict(manifest_path=tmp_path/"sample.json", start_time=stamp-1, end_time=stamp+1,
                matches_per_player=2, sampling_seed=42)


def test_roster_route_and_missing_puuids():
    session = Mock(headers={})
    session.get.return_value = Mock(status_code=200, json=Mock(return_value={}), headers={})
    c = RiotClient("test-only", session=session, interval=0)
    c.challenger_roster("na1")
    assert session.get.call_args.args[0] == "https://na1.api.riotgames.com/lol/league/v4/challengerleagues/by-queue/RANKED_SOLO_5x5"
    c.match("NA1_1")
    assert session.get.call_args.args[0].startswith("https://americas.api.riotgames.com/")
    with pytest.raises(ConfigurationError): c.challenger_roster("kr")
    with pytest.raises(InputError): roster_players(dict(tier="CHALLENGER", queue="RANKED_SOLO_5x5", entries=[{}]))
    assert roster_players(dict(tier="CHALLENGER", queue="RANKED_SOLO_5x5", entries=[dict(puuid="b"),dict(puuid="a")])) == ["a","b"]


def test_history_exact_cap_and_overflow():
    c = RiotClient("test-only", interval=0)
    c.get = Mock(side_effect=[[f"NA1_{i}" for i in range(100)], []])
    assert len(c.history_pool("p", start_time=1, end_time=2, limit=100)) == 100
    assert c.get.call_args.args[1]["count"] == 1
    c.get = Mock(side_effect=[[f"NA1_{i}" for i in range(100)], ["NA1_101"]])
    with pytest.raises(ConfigurationError, match="exceeds"):
        c.history_pool("p", start_time=1, end_time=2, limit=100)


def test_shuffling_is_order_independent_and_reproducible():
    ids = [str(i) for i in range(50)]
    a = shuffled_pool(ids, 42, "player")
    assert a == shuffled_pool(list(reversed(ids)), 42, "player")
    assert set(a) == set(ids) and a != ids
    assert a != shuffled_pool(ids, 43, "player")
    assert a != shuffled_pool(ids, 42, "other-player")


@pytest.mark.parametrize("mutation,reason", [("patch","patch_mismatch"),("time","outside_time_window"),
    ("queue","queue_mismatch"),("champion","invalid_champions"),("seed","seed_not_in_match"),
    ("short","remake_or_short_game")])
def test_detail_eligibility(payloads, mutation, reason):
    payload = payloads[0]
    payload["metadata"]["matchId"] = "NA1_1"
    stamp = payload["info"]["gameStartTimestamp"]//1000
    cfg = dict(platform="na1", start_time=stamp-1,end_time=stamp+1,min_duration=300,patch=None)
    puuid = "synthetic-player-1"
    if mutation=="patch": cfg["patch"]="99.99"
    if mutation=="time": cfg["end_time"]=stamp
    if mutation=="queue": payload["info"]["queueId"]=440
    if mutation=="champion": payload["info"]["participants"][1]["championId"]=1
    if mutation=="seed": puuid="absent"
    if mutation=="short": payload["info"]["gameDuration"]=100
    assert detail_reason(payload,"NA1_1",puuid,cfg)==reason


def test_collects_entire_roster_and_only_frozen_unique_ids(db, payloads, tmp_path):
    c = FakeClient(payloads, size=2)
    opts = options(tmp_path,payloads)
    db.enqueue("americas","UNRELATED_99")
    result = collect_challenger(db,c,**opts)
    assert result["status"]=="complete"
    metrics=result["metrics"]
    assert metrics["roster_players"]==2 and metrics["player_match_selections"]==4
    assert metrics["unique_selected_matches"]==2 and metrics["overlap_removed"]==2
    assert len([x for x in c.calls if x[0]=="timeline"])==2
    assert len([x for x in c.calls if x[0]=="match"])==2
    assert not any(x[1]=="UNRELATED_99" for x in c.calls)
    rows=list(db.conn.execute("SELECT * FROM sampling_matches"))
    assert len(rows)==2 and all(r["source_players"]==2 for r in rows)
    manifest=json.loads(opts["manifest_path"].read_text())
    assert "roster_snapshot" in manifest and manifest["phase"]=="complete"
    assert len(manifest["players"])==2
    c2=FakeClient(payloads,size=50)
    again=collect_challenger(db,c2,**opts)
    assert again["sample_id"]==result["sample_id"] and c2.calls==[]
    assert db.counts("americas")["matches"]==2
    report=champion_audit(db,sample_id=result["sample_id"])
    assert report["eligible_matches"]==2 and report["scope"]["sample_id"]==result["sample_id"]


def test_sampling_checkpoint_survives_api_outage(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    c=FakeClient(payloads,fail_detail_at=2)
    with pytest.raises(RiotError): collect_challenger(db,c,**opts)
    saved=json.loads(opts["manifest_path"].read_text())
    first=next(iter(saved["players"].values()))
    assert len(first["selected_match_ids"])==1 and first["cursor"]==1
    c2=FakeClient(payloads)
    result=collect_challenger(db,c2,**opts)
    assert result["status"]=="complete"
    assert not any(x[0]=="roster" for x in c2.calls)
    # Frozen first player's history is not rediscovered after the outage.
    assert ("history",sorted(c2.puuids)[0]) not in c2.calls
    resumed=json.loads(opts["manifest_path"].read_text())
    assert resumed["sample_id"]==saved["sample_id"]
    assert first["selected_match_ids"][0] in resumed["selected_match_ids"]


def test_manifest_change_rejected(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    collect_challenger(db,FakeClient(payloads),**opts)
    with pytest.raises(InputError,match="configuration differs"):
        collect_challenger(db,FakeClient(payloads),**{**opts,"sampling_seed":43})
    contents=json.loads(opts["manifest_path"].read_text())
    contents["selected_match_ids"]=["NA1_999"]
    opts["manifest_path"].write_text(json.dumps(contents))
    with pytest.raises(InputError,match="Invalid sampling manifest"):
        collect_challenger(db,FakeClient(payloads),**opts)


def test_player_shortfalls_reported_without_replacement(db,payloads,tmp_path):
    result=collect_challenger(db,FakeClient(payloads,size=1),**options(tmp_path,payloads))
    assert result["status"]=="partial" and result["metrics"]["players_with_shortfalls"]==2
    assert result["metrics"]["unique_selected_matches"]==1


def test_bad_timeline_keeps_membership_and_resumes_same_sample(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    result=collect_challenger(db,FakeClient(payloads,size=1,fail_timeline=True),**opts)
    assert result["status"]=="partial" and result["metrics"]["failed"]==1
    before=json.loads(opts["manifest_path"].read_text())["selected_match_ids"]
    assert db.conn.execute("SELECT count(*) AS n FROM sampling_matches").fetchone()["n"]==1
    collect_challenger(db,FakeClient(payloads,size=20),**opts)
    assert json.loads(opts["manifest_path"].read_text())["selected_match_ids"]==before
    assert db.counts("americas")["matches"]==1


def test_rejected_details_are_replaced_with_next_eligible_candidate(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    c=FakeClient(payloads,size=4,players=1)
    first=shuffled_pool(c.ids,42,c.puuids[0])[0]
    bad=c.match(first)
    bad["info"]["queueId"]=440
    db.save_raw("americas",first,"match",bad)
    result=collect_challenger(db,c,**opts)
    manifest=json.loads(opts["manifest_path"].read_text())
    player=manifest["players"][c.puuids[0]]
    assert player["rejections"]=={first:"queue_mismatch"}
    assert first not in manifest["selected_match_ids"] and result["metrics"]["accepted"]==2


def test_missing_sample_audit_fails_clearly(db):
    from uuid import uuid4
    with pytest.raises(InputError,match="not found"): champion_audit(db,sample_id=str(uuid4()))


def test_sampling_lock_released_after_error(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    with pytest.raises(RiotError):
        collect_challenger(db,FakeClient(payloads,fail_detail_at=1),**opts)
    assert collect_challenger(db,FakeClient(payloads),**opts)["status"]=="complete"


def test_challenger_cli_requires_explicit_window(capsys):
    from src.ingestion.__main__ import main
    with pytest.raises(SystemExit): main(["collect-challenger"])
    assert "fixed start/end" in capsys.readouterr().err


def test_default_ten_samples_without_fetching_all_candidate_details(db,payloads,tmp_path):
    opts=options(tmp_path,payloads)
    opts.pop("matches_per_player")
    c=FakeClient(payloads,size=50,players=1)
    result=collect_challenger(db,c,**opts)
    assert result["metrics"]["player_match_selections"]==10
    assert len([x for x in c.calls if x[0]=="match"])==10
    assert len([x for x in c.calls if x[0]=="timeline"])==10


def test_cli_dates_and_sample_audit(db,payloads,tmp_path,monkeypatch,capsys):
    from contextlib import nullcontext
    from datetime import datetime,timezone,timedelta
    import importlib
    mod=importlib.import_module("src.ingestion.__main__")
    c=FakeClient(payloads,size=2)
    monkeypatch.setattr(mod,"RiotClient",lambda *args,**kwargs:c)
    monkeypatch.setattr(mod,"Database",lambda _:nullcontext(db))
    monkeypatch.setattr(mod,"database_url",lambda:"unused")
    dt=datetime.fromtimestamp(payloads[0]["info"]["gameStartTimestamp"]//1000,timezone.utc)
    code=mod.main(["collect-challenger","--platform","NA1","--start-date",(dt-timedelta(days=1)).strftime("%Y-%m-%d"),
                   "--end-date",(dt+timedelta(days=1)).strftime("%Y-%m-%d"),"--matches-per-player","2",
                   "--manifest",str(tmp_path/"cli.json")])
    assert code==0
    output=capsys.readouterr()
    report=json.loads(output.out)
    assert "Sampling player" in output.err and "synthetic-player" not in output.err
    assert mod.main(["audit","--sample-id",report["sample_id"]])==0
    audit=json.loads(capsys.readouterr().out)
    assert audit["eligible_matches"]==2
