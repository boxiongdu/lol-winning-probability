ARG AIRFLOW_VERSION=3.1.8
FROM apache/airflow:${AIRFLOW_VERSION}-python3.11
ARG AIRFLOW_VERSION

# Pin Airflow itself so the project requirements can never upgrade or downgrade it.
COPY requirements.txt requirements-modeling.txt /tmp/
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
    -r /tmp/requirements.txt -r /tmp/requirements-modeling.txt
