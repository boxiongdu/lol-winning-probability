FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /opt/rift
COPY requirements-app.txt /tmp/requirements-app.txt
RUN pip install -r /tmp/requirements-app.txt
# Source, configs and models are bind-mounted by compose.yaml so the app always
# reads the model the pipeline last published.
EXPOSE 8501
CMD ["streamlit", "run", "app/streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
