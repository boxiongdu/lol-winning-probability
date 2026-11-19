-- A's view supplies complete nonterminal frames. Lateral counts include ties.
SELECT f.match_id, m.game_start_ms, m.blue_win, f.timestamp_ms, f.team_id,
       f.total_gold, c.kills, c.towers_destroyed, c.dragons_secured, c.barons_secured
FROM team_frame_states f
JOIN matches m USING (match_id)
CROSS JOIN LATERAL (
    SELECT count(*) FILTER (WHERE event_type = 'CHAMPION_KILL') AS kills,
           count(*) FILTER (WHERE event_type = 'BUILDING_KILL'
               AND payload->>'buildingType' = 'TOWER_BUILDING') AS towers_destroyed,
           count(*) FILTER (WHERE event_type = 'ELITE_MONSTER_KILL'
               AND payload->>'monsterType' = 'DRAGON') AS dragons_secured,
           count(*) FILTER (WHERE event_type = 'ELITE_MONSTER_KILL'
               AND payload->>'monsterType' = 'BARON_NASHOR') AS barons_secured
    FROM events e
    WHERE e.match_id = f.match_id AND e.timestamp_ms <= f.timestamp_ms
      AND e.credited_team_id = f.team_id
) c
WHERE f.match_id <> 'DEMO_1'
ORDER BY m.game_start_ms, f.match_id, f.timestamp_ms, f.team_id
