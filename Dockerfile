FROM python:3.13-slim
WORKDIR /app
# LaTeX on cards ([latex], [$], [$$]): latex + dvipng (dvisvgm comes with texlive-binaries).
# Adds roughly 300-400 MB to the image; without it cards show the LaTeX source instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base texlive-latex-recommended dvipng \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir anki fastapi "uvicorn[standard]" python-multipart itsdangerous pywebpush tzdata
COPY app.py .
COPY flashcards ./flashcards
COPY addons_examples ./addons_examples
COPY bundled_addons ./bundled_addons
ENV DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
