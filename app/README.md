# Interactive application

Member D owns the map, event/action controls, state summary, probability history,
restart and final recap.

```bash
python -m pip install -r requirements-app.txt
streamlit run app/streamlit_app.py
```

`session.py` holds the decision loop and is independent of Streamlit;
`map_view.py` draws the map; `streamlit_app.py` is the page. See
[docs/member_d_week8.md](../docs/member_d_week8.md).
