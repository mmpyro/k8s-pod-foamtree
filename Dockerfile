FROM python:3.8.11-slim
WORKDIR /code
RUN apt-get update && apt-get install -y make
COPY . .
RUN make restore
RUN make tests
RUN make build install