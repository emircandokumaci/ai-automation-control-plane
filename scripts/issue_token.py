#!/usr/bin/env python3
import hashlib,secrets,sys
from controlplane.db import SessionLocal
from controlplane.models import ServiceToken
name=sys.argv[1] if len(sys.argv)>1 else "integration"
scopes=(sys.argv[2].split(',') if len(sys.argv)>2 else ["jobs:read","jobs:write","events:write"])
token="cp_"+secrets.token_urlsafe(32)
with SessionLocal() as db:
    db.add(ServiceToken(name=name,token_hash=hashlib.sha256(token.encode()).hexdigest(),scopes=scopes)); db.commit()
print(token)
