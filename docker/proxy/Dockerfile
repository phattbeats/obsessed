FROM python:3.12-slim
WORKDIR /app
COPY proxy_server.py .
ENV PROXY_PORT=8888
EXPOSE 8888
CMD ["python", "proxy_server.py"]
