FROM mcr.microsoft.com/playwright/python:v1.51.0-noble
WORKDIR /app
COPY server.py .
RUN pip install aiohttp playwright==1.51.0
EXPOSE 3000
CMD ["python", "server.py"]
