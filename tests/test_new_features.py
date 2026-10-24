import concurrent.futures, threading, time, uuid
from fastapi.testclient import TestClient
from app import main, decision_engine
from app.storage import Store
HEAD={'Authorization':'Bearer alpha','A2A-Version':'1.0','Content-Type':'application/a2a+json'}
REFS=['[R1]','[R2]','[R3]']
def pkg():return {'packageId':'p1','documents':[{'text':'valid invoice [R1] verified [R2] within authority [R3]'}],'invoice':{'vendorName':'A','invoiceNumber':'I','amountMinor':10,'currency':'INR'}}
def body(mid):return {'message':{'messageId':mid,'role':'ROLE_USER','parts':[{'mediaType':main.INPUT_MODE,'data':{'batchId':'b','policyRevision':'r','packages':[pkg()]}}]}}
def test_concurrent_and_phase4(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'store',Store(str(tmp_path/'db.sqlite')))
    monkeypatch.setattr(main,'TOKENS',['alpha']);monkeypatch.setattr(main,'DEMO',False)
    count=[0];lock=threading.Lock()
    def model(packages,revision):
        with lock:count[0]+=1
        time.sleep(.15)
        return [{'packageId':'p1','action':'settle_invoice','facts':pkg()['invoice'],'evidenceRefs':REFS,'rationale':'settle_invoice is supported by [R1] and [R2], and [R3] confirms approval is unnecessary.'}]
    monkeypatch.setattr(decision_engine,'ask_model',model)
    def post(i):return TestClient(main.app).post('/a2a/message:send',headers=HEAD,json=body('same'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        responses=list(pool.map(post,range(8)))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    assert count[0]==1
    assert len({r.json()['task']['id'] for r in responses})==1
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        responses2=list(pool.map(lambda i:TestClient(main.app).post('/a2a/message:send',headers=HEAD,json=body('different'+str(i))),range(6)))
    assert all(r.status_code==200 for r in responses2)
    assert count[0]==1
    task=responses[0].json()['task'];p=task['artifacts'][0]['parts'][0]['data']['proposals'][0]
    continuation={'message':{'messageId':'result1','role':'ROLE_USER','taskId':task['id'],'contextId':task['contextId'],'parts':[{'mediaType':main.RESULT_MODE,'data':{'batchId':'b','results':[{'packageId':'p1','actionId':p['actionId'],'action':p['action'],'outcome':'ACCEPTED','receiptNonce':'nonce1'}]}}]}}
    result=TestClient(main.app).post('/a2a/message:send',headers=HEAD,json=continuation)
    assert result.status_code==200,result.text
    assert result.json()['task']['status']['state']=='TASK_STATE_COMPLETED'
    assert len(result.json()['task']['artifacts'][1]['parts'][0]['data']['receipts'])==1
    assert TestClient(main.app).post('/a2a/tasks/'+task['id']+':cancel',headers=HEAD).status_code==409
