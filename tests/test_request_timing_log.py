import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.observability import install_observability


def test_request_log_names_the_route_template_not_the_raw_url(caplog):
    app = FastAPI()
    install_observability(app)

    @app.get('/api/v1/student/lectures/{lecture_id}')
    async def lecture(lecture_id: str):
        return {'id': lecture_id}

    with caplog.at_level(logging.INFO, logger='api.http'):
        response = TestClient(app).get('/api/v1/student/lectures/private-lecture-id?token=secret')

    assert response.status_code == 200
    assert response.headers['Server-Timing'].startswith('app;dur=')
    entry = json.loads([r.getMessage() for r in caplog.records if r.name == 'api.http'][-1])
    assert entry['route'] == '/api/v1/student/lectures/{lecture_id}'
    assert 'private-lecture-id' not in json.dumps(entry)
    assert 'secret' not in json.dumps(entry)
    assert entry['duration_ms'] >= 0
