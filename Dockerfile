# ベースイメージにPython 3.11を使用
FROM python:3.11-slim

# システムパッケージのアップデートとFFmpegのインストール
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 作業ディレクトリを作成
WORKDIR /app

# 依存関係ファイルをコピーしてインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 全てのソースコードをコピー
COPY . .

# templatesディレクトリが存在することを確認（デバッグ用）
RUN ls -R /app/templates

# Renderのポート環境変数に対応して起動
CMD gunicorn --bind 0.0.0.0:$PORT api.index:app
