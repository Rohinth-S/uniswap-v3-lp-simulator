FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
COPY src ./src
RUN pip install --no-cache-dir .
ENTRYPOINT ["uniswap_v3_lp_simulator"]
