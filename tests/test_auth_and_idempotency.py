import asyncio
import base64
import hashlib
import hmac
import io
import json
import time
import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile, Headers
from app.api import deps
from app.services.idempotency import fingerprint_upload, scoped_key

def token(role='student', exp=None, **extra):
    payload=base64.urlsafe_b64encode(json.dumps({'email':'student@example.test','role':role,'exp':exp or time.time()+300,**extra}).encode()).rstrip(b'=')
    signature=base64.urlsafe_b64encode(hmac.digest(b'test-secret', payload,'sha256')).rstrip(b'=')
    return 'Bearer '+(payload+b'.'+signature).decode()

@pytest.fixture(autouse=True)
def secret(monkeypatch): monkeypatch.setattr(deps.settings,'internal_api_key','test-secret')

def test_signed_role_and_student_identity():
    assert deps.verify_token(token())['role']=='student'
    assert asyncio.run(deps.trusted_student_email_dep(authorization=token()))=='student@example.test'
    assert asyncio.run(deps.admin_dep(token('admin')))['role']=='admin'

@pytest.mark.parametrize('value',[None,'Bearer bad','Bearer a.b',token(exp=1)])
def test_reject_untrusted_or_expired(value):
    with pytest.raises(HTTPException) as error: deps.verify_token(value)
    assert error.value.status_code==401

def test_student_cannot_admin():
    with pytest.raises(HTTPException) as error: asyncio.run(deps.admin_dep(token()))
    assert error.value.status_code==403

def upload(data): return UploadFile(io.BytesIO(data),filename='lecture.wav',headers=Headers({'content-type':'audio/wav'}))

def test_fingerprint_repeat_change_and_course_scope():
    async def compute(scope,data):
        file=upload(data)
        result=await fingerprint_upload({'scope':scope},[('source',file)])
        assert await file.read()==data  # hash inspection must rewind originals
        return result
    first=asyncio.run(compute('course-a',b'lecture'))
    assert first==asyncio.run(compute('course-a',b'lecture'))
    assert first!=asyncio.run(compute('course-b',b'lecture'))
    assert first!=asyncio.run(compute('course-a',b'changed'))
    assert scoped_key('course-a','abc')!=scoped_key('course-b','abc')

@pytest.mark.parametrize('role',['admin','student'])
def test_demo_tokens_are_read_only(role):
    demo=token(role,demo=True)
    assert deps.verify_token(demo)['demo'] is True
    for method in ('GET','HEAD','OPTIONS'): assert not deps.demo_write_blocked(method,demo)
    for method in ('POST','PUT','PATCH','DELETE'): assert deps.demo_write_blocked(method,demo)

def test_real_and_invalid_tokens_are_not_demo_blocked():
    assert not deps.demo_write_blocked('POST',token('admin'))
    assert not deps.demo_write_blocked('POST','Bearer bad')
    assert not deps.demo_write_blocked('POST',None)

def test_malformed_demo_claim_rejected():
    with pytest.raises(HTTPException) as error: deps.verify_token(token('admin',demo='yes'))
    assert error.value.status_code==401
