# Airflow image + what the pipeline needs.
FROM apache/airflow:3.3.2-python3.12

# Pipeline code (extract, load) and pytest. Pinning apache-airflow to its own version
# stops pip from upgrading or downgrading Airflow while installing the other packages.
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
    "sqlalchemy>=2.0" "pymysql>=1.1" "pyarrow>=15" "python-dotenv>=1.0" "psycopg2-binary>=2.9" pytest

# dbt gets its own virtual environment, so its packages never conflict with Airflow's.
# Same versions as requirements-dbt.txt on the laptop.
RUN python -m venv /home/airflow/dbt-venv \
    && /home/airflow/dbt-venv/bin/pip install --no-cache-dir "dbt-core==1.12.6" "dbt-postgres==1.11.0"
