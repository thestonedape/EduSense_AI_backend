import json
import logging
import re
import time
import uuid
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse, Response
metrics = {'requests':0, 'errors':0, 'duration_seconds':0.0}
latency_bounds = (.005,.01,.025,.05,.1,.25,.5,1,2,5,10)
latency_counts = [0] * len(latency_bounds)

class JSONFormatter(logging.Formatter):
    def format(self, record):
        message = record.getMessage()
        try:
            payload = json.loads(message)
            if not isinstance(payload, dict): payload = {'event':message}
        except (ValueError, TypeError): payload = {'event':message}
        payload.update(level=record.levelname, logger=record.name)
        if record.exc_info: payload['exception_type'] = record.exc_info[0].__name__
        return json.dumps(payload)

def install_observability(app):
    if not logging.getLogger().handlers: logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers: handler.setFormatter(JSONFormatter())
    @app.middleware('http')
    async def observe(request, call_next):
        supplied = request.headers.get('X-Request-ID', '')
        request_id = supplied if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', supplied) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse({'detail': 'Internal service error', 'request_id': request_id}, status_code=500)
        elapsed = time.perf_counter() - started
        metrics['requests'] += 1
        metrics['errors'] += int(response.status_code >= 400)
        metrics['duration_seconds'] += elapsed
        for index, bound in enumerate(latency_bounds): latency_counts[index] += int(elapsed <= bound)
        response.headers['X-Request-ID'] = request_id
        logging.getLogger('api.http').info(json.dumps({'event': 'request', 'request_id': request_id, 'method': request.method, 'status': response.status_code, 'duration_ms': round(elapsed * 1000, 3)}))
        return response
    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'detail': exc.detail, 'request_id': getattr(request.state, 'request_id', None)}, status_code=exc.status_code, headers=exc.headers)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': 'Invalid request', 'request_id': getattr(request.state, 'request_id', None)}, status_code=422)
    @app.get('/metrics', include_in_schema=False)
    def prometheus_metrics():
        rows=[f'edusense_{name} {value}' for name, value in metrics.items()]
        rows += [f'edusense_request_duration_seconds_bucket{{le="{bound}"}} {count}' for bound,count in zip(latency_bounds,latency_counts)]
        rows += [f'edusense_request_duration_seconds_bucket{{le="+Inf"}} {metrics["requests"]}',f'edusense_request_duration_seconds_count {metrics["requests"]}',f'edusense_request_duration_seconds_sum {metrics["duration_seconds"]}']
        return Response('\n'.join(rows) + '\n', media_type='text/plain; version=0.0.4')
