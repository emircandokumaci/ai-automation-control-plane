import time
from controlplane.security.signatures import sign_payload,verify_signature
def test_signature_and_replay_window():
    ts=str(int(time.time())); eid='evt-1'; body=b'{}'; secret='test'
    sig=sign_payload(secret,ts,eid,body); assert verify_signature(secret,ts,eid,body,sig); assert not verify_signature(secret,'1',eid,body,sig)
