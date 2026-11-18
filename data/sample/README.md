# Synthetic offline plumbing fixture

DEMO_1.match.json and DEMO_1.timeline.json are hand-generated API-shaped examples,
not actual Riot API responses. All player identifiers and statistics are synthetic.
They contain 10 participants, 21 frames, a kill, dragon, tower and GAME_END event.
The first participant's initial position is intentionally missing.

Use only for storage/validation/interface tests, never training or reported findings.
Load into a separate demo database using:

```bash
python -m src.ingestion load-fixture --directory data/sample
```

Expected counts: 1 match, 2 raw responses, 10 participants, 210 frames, 4 events.
Real collection must use a clean database so this record does not count toward
its target. A reviewed real offline dataset remains a Week 9 deliverable.
