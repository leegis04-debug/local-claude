ARG BASE_IMAGE=local-claude/ops-ml:latest
FROM ${BASE_IMAGE}

WORKDIR /workspace

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY . .

EXPOSE 8001
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
