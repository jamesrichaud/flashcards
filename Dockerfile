FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir anki fastapi "uvicorn[standard]" python-multipart itsdangerous
COPY app.py .
COPY flashcards ./flashcards
COPY addons_examples ./addons_examples
COPY bundled_addons ./bundled_addons
ENV DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
