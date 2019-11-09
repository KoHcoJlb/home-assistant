FROM python:3-alpine

RUN apk add gcc musl-dev libffi-dev openssl-dev

COPY setup.py /app/
COPY homeassistant /app/homeassistant
RUN pip install -e /app

ENTRYPOINT ["hass", "-c", "/config"]
