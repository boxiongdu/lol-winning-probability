import pytest
from src.ingestion.normalize import ValidationError, normalize, exclusion


def test_normalization_and_objective_attribution(payloads):
    record, players, frames, events = normalize(*payloads, "DEMO_1", "americas")
    assert record["blue_win"] is True
    assert len(players) == 10 and len(frames) == 210
    assert frames[0][3:5] == (None, None)
    tower = next(e for e in events if e[4] == "BUILDING_KILL")
    assert tower[5] is None and tower[7] == 100
    assert exclusion(record) is None


@pytest.mark.parametrize("change", ["missing_player", "same_timestamp", "wrong_id", "wrong_puuid", "negative_gold"])
def test_invalid_data_rejected(payloads, change):
    match, timeline = payloads
    if change == "missing_player":
        del timeline["info"]["frames"][0]["participantFrames"]["1"]
    elif change == "same_timestamp":
        timeline["info"]["frames"][1]["timestamp"] = 0
    elif change == "wrong_id":
        match["metadata"]["matchId"] = "OTHER"
    elif change == "wrong_puuid":
        timeline["info"]["participants"][0]["puuid"] = "OTHER"
    else:
        timeline["info"]["frames"][0]["participantFrames"]["1"]["totalGold"] = -1
    with pytest.raises(ValidationError):
        normalize(match, timeline, "DEMO_1", "americas")


def test_exclusion_policy(payloads):
    record = normalize(*payloads, "DEMO_1", "americas")[0]
    assert exclusion(record, queue=440) == "queue_mismatch"
    assert exclusion(record, map_id=12) == "map_mismatch"
    record["early_surrender"] = True
    assert exclusion(record) == "remake_or_short_game"
