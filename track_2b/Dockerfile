# Treuhand Shield proxy + demo. No model weights in the image: llama.cpp serves them from a volume.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY src ./src
COPY data ./data
COPY tests ./tests
CMD ["python", "src/demo.py"]
