from fastapi import HTTPException, Depends, Query, Request, Header
import json, uuid

def register(app, db, now, current_user, audit_event, api_error=None):
    def forbidden(message='无装配操作权限'):
        if api_error: raise api_error(403, 'ASSEMBLY_TASK_FORBIDDEN', message)
        raise HTTPException(403, message)
    def actor(u):
        if u['role'] != 'ASSEMBLER': forbidden()
    def task(c, tid, u):
        r=c.execute('SELECT * FROM assembly_tasks WHERE id=?',(tid,)).fetchone()
        if not r: raise HTTPException(404,'任务不存在')
        if r['assigned_assembler_id'] != u['id']: forbidden('只能操作本人任务')
        return r
    def op(body, key):
        client = str(body.get('clientOperationId', '')).strip()
        # Keep legacy callers working when they supplied the body operation id
        # but omitted the header; a supplied header must still match exactly.
        if not client or (key and client.lower() != key.lower()):
            if api_error: raise api_error(400, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key 与 clientOperationId 不一致')
            raise HTTPException(400, 'Idempotency-Key 与 clientOperationId 不一致')
        return key or client
    def result_task(r):
        return {'id':r['id'],'orderNo':r['order_no'],'deviceId':r['device_id'],'deviceNo':r['device_no'],'assignedAssemblerId':r['assigned_assembler_id'],'status':r['status'],'progressStage':r['progress_stage'],'taskVersion':r['task_version'],'serverTime':now()}
    def finish(c, rid):
        t=now(); r=c.execute('SELECT started_at FROM labor_records WHERE id=?',(rid,)).fetchone(); secs=max(0, int(__import__('datetime').datetime.fromisoformat(t).timestamp()-__import__('datetime').datetime.fromisoformat(r['started_at']).timestamp()))
        c.execute("UPDATE labor_records SET status='COMPLETED',ended_at=?,duration_minutes=? WHERE id=?",(t,secs//60,rid))
    def bodycheck(b):
        if not isinstance(b,dict): raise HTTPException(422,'请求参数无效')
    @app.get('/api/v1/assembly/tasks')
    def list_tasks(page:int=Query(1,ge=1), pageSize:int=Query(20,ge=1,le=100), user=Depends(current_user)):
        c=db(); where='assigned_assembler_id=?' if user['role']=='ASSEMBLER' else '1=1'; args=[user['id']] if user['role']=='ASSEMBLER' else []
        total=c.execute(f'SELECT count(*) n FROM assembly_tasks WHERE {where}',args).fetchone()['n']; rows=c.execute(f'SELECT * FROM assembly_tasks WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?',args+[pageSize,(page-1)*pageSize]).fetchall(); c.close()
        return {'items':[result_task(r) for r in rows],'page':page,'pageSize':pageSize,'total':total,'totalPages':(total+pageSize-1)//pageSize}
    def conflict(code):
        if api_error: raise api_error(409, code, code)
        raise HTTPException(409, code)
    def mutate(tid, body, user, request, key, action, target, transition):
        actor(user); bodycheck(body); c=db(); r=task(c,tid,user)
        expected=body.get('expectedVersion')
        if expected is not None and expected != r['task_version']:
            c.close(); conflict('TASK_VERSION_CONFLICT')
        opid=op(body,key)
        prior=c.execute('SELECT result_json FROM assembly_operations WHERE client_operation_id=?',(opid,)).fetchone()
        if prior: c.close(); return json.loads(prior['result_json'])
        if not transition(r): c.close(); conflict('ASSEMBLY_TASK_STATE_CONFLICT')
        before = result_task(r)
        out=target(c,r,user,opid)
        event_names = {'accept':'MATERIAL_ACCEPTED_FOR_ASSEMBLY','start':'ASSEMBLY_STARTED','progress':'ASSEMBLY_PROGRESS_UPDATED','complete':'ASSEMBLY_COMPLETED'}
        if action in event_names:
            audit_event(c, event_names[action], tid, user, request.headers.get('X-Request-Id', ''), opid, before, out, request, entity_type='ASSEMBLY_TASK')
        c.execute('INSERT INTO assembly_operations VALUES(?,?,?)',(opid,json.dumps(out,ensure_ascii=False),now())); c.commit(); c.close(); return out
    @app.post('/api/v1/assembly/tasks/{tid}/accept-material')
    def accept(tid:str, body:dict, request:Request, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        def go(c,r,u,o):
            t=now(); c.execute("UPDATE assembly_tasks SET status='MATERIAL_ACCEPTED',material_accepted_at=?,task_version=task_version+1,updated_at=? WHERE id=?",(t,t,tid)); x=dict(c.execute('SELECT * FROM assembly_tasks WHERE id=?',(tid,)).fetchone()); return result_task(x)
        return mutate(tid,body,user,request,idempotency_key,'accept',go,lambda r:r['status']=='WAITING_MATERIAL')
    @app.post('/api/v1/assembly/tasks/{tid}/start')
    def start(tid:str,body:dict,request:Request,user=Depends(current_user),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        def go(c,r,u,o):
            rid='lr_'+uuid.uuid4().hex; t=now(); c.execute("INSERT INTO labor_records VALUES(?,?,?,?,?,?,?,?,?,?,?)",(rid,tid,u['id'],'ASSEMBLY','ACTIVE',t,None,None,None,o,t)); c.execute("UPDATE assembly_tasks SET status='IN_PROGRESS',task_version=task_version+1,updated_at=? WHERE id=?",(t,tid)); return {'id':rid,'laborRecordId':rid,'type':'ASSEMBLY','status':'ACTIVE','startedAt':t,'taskVersion':r['task_version']+1,'serverTime':t}
        return mutate(tid,body,user,request,idempotency_key,'start',go,lambda r:r['status'] in ('MATERIAL_ACCEPTED','PAUSED_FOR_TEMPORARY_TRANSFER'))
    @app.post('/api/v1/assembly/tasks/{tid}/progress')
    def progress(tid:str,body:dict,request:Request,user=Depends(current_user),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        def go(c,r,u,o):
            stage=body.get('stage');
            if stage not in (1,2,3) or stage != r['progress_stage']+1: raise HTTPException(409,'PROGRESS_ORDER_CONFLICT')
            t=now(); v=r['task_version']+1; c.execute('UPDATE assembly_tasks SET progress_stage=?,task_version=?,updated_at=? WHERE id=?',(stage,v,t,tid)); c.execute('INSERT INTO progress_events(id,task_id,worker_user_id,from_stage,to_stage,task_version,server_time,client_operation_id) VALUES(?,?,?,?,?,?,?,?)',('pe_'+uuid.uuid4().hex,tid,u['id'],stage-1,stage,v,t,o)); return result_task(dict(c.execute('SELECT * FROM assembly_tasks WHERE id=?',(tid,)).fetchone()))
        return mutate(tid,body,user,request,idempotency_key,'progress',go,lambda r:r['status']=='IN_PROGRESS')
    @app.post('/api/v1/assembly/tasks/{tid}/complete')
    def complete(tid:str,body:dict,request:Request,user=Depends(current_user),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        def go(c,r,u,o):
            lr=c.execute("SELECT id FROM labor_records WHERE task_id=? AND worker_user_id=? AND status='ACTIVE'",(tid,u['id'])).fetchone()
            if not lr: raise HTTPException(409,'NO_ACTIVE_LABOR')
            finish(c,lr['id']); t=now(); c.execute("UPDATE assembly_tasks SET status='COMPLETED',completed_at=?,task_version=task_version+1,updated_at=? WHERE id=?",(t,t,tid)); return result_task(dict(c.execute('SELECT * FROM assembly_tasks WHERE id=?',(tid,)).fetchone()))
        return mutate(tid,body,user,request,idempotency_key,'complete',go,lambda r:r['status']=='IN_PROGRESS')
    @app.post('/api/v1/assembly/temporary-transfers/start')
    def transfer_start(body:dict,request:Request,user=Depends(current_user),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        actor(user); bodycheck(body); remark=body.get('remark','')
        if not isinstance(remark,str) or not 1<=len(remark)<=500: raise HTTPException(422,'remark 必填')
        o=op(body,idempotency_key)
        c=db(); prior=c.execute('SELECT id FROM temporary_transfers WHERE client_operation_id=?',(o,)).fetchone()
        if prior: r=c.execute('SELECT l.* FROM labor_records l JOIN temporary_transfers t ON t.labor_record_id=l.id WHERE t.id=?',(prior['id'],)).fetchone(); c.close(); return {'temporaryTransferId':prior['id'],'type':'TEMPORARY_TRANSFER','status':r['status'],'serverTime':now()}
        tid=body.get('taskId'); source=task(c,tid,user) if tid else None
        active=c.execute("SELECT id,task_id FROM labor_records WHERE worker_user_id=? AND status='ACTIVE'",(user['id'],)).fetchone()
        if active:
            if active['task_id']: finish(c,active['id']); c.execute("UPDATE assembly_tasks SET status='PAUSED_FOR_TEMPORARY_TRANSFER',updated_at=? WHERE id=?",(now(),active['task_id']))
            else: c.close(); raise HTTPException(409,'ACTIVE_TRANSFER_EXISTS')
        t=now(); rid='lr_'+uuid.uuid4().hex; xid='tt_'+uuid.uuid4().hex; c.execute("INSERT INTO labor_records VALUES(?,?,?,?,?,?,?,?,?,?,?)",(rid,None,user['id'],'TEMPORARY_TRANSFER','ACTIVE',t,None,None,remark,o,t)); c.execute('INSERT INTO temporary_transfers VALUES(?,?,?,?,?,?,?,?,?)',(xid,user['id'],tid,rid,'ACTIVE',remark,t,None,o)); c.commit(); c.close(); return {'temporaryTransferId':xid,'laborRecordId':rid,'type':'TEMPORARY_TRANSFER','status':'ACTIVE','startedAt':t,'serverTime':t}
    @app.post('/api/v1/assembly/temporary-transfers/{xid}/complete')
    def transfer_complete(xid:str,body:dict,request:Request,user=Depends(current_user),idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        actor(user); bodycheck(body); o=op(body,idempotency_key); c=db(); tr=c.execute('SELECT * FROM temporary_transfers WHERE id=? AND worker_user_id=?',(xid,user['id'])).fetchone()
        if not tr: c.close(); raise HTTPException(404,'调拨不存在')
        if tr['status']=='COMPLETED': c.close(); return {'temporaryTransferId':xid,'status':'COMPLETED'}
        remark=body.get('remark','');
        if not 1<=len(remark)<=500: c.close(); raise HTTPException(422,'remark 必填')
        finish(c,tr['labor_record_id']); t=now(); c.execute("UPDATE temporary_transfers SET status='COMPLETED',ended_at=?,remark=? WHERE id=?",(t,remark,xid)); c.execute("UPDATE labor_records SET remark=? WHERE id=?",(remark,tr['labor_record_id'])); c.commit(); c.close(); return {'temporaryTransferId':xid,'status':'COMPLETED','serverTime':t}
    @app.get('/api/v1/workshop/summary')
    def summary(user=Depends(current_user)):
        if user['role'] not in ('WORKSHOP_SUPERVISOR','ADMIN'): raise HTTPException(403,'无统计权限')
        c=db(); t=c.execute('SELECT count(*) n, sum(status="COMPLETED") done, coalesce(sum(progress_stage),0) p FROM assembly_tasks').fetchone(); a=c.execute("SELECT coalesce(sum(duration_minutes),0) n FROM labor_records WHERE type='ASSEMBLY'").fetchone(); x=c.execute("SELECT coalesce(sum(duration_minutes),0) n FROM labor_records WHERE type='TEMPORARY_TRANSFER'").fetchone(); c.close(); total=t['n']; return {'totalTasks':total,'completedTasks':t['done'] or 0,'overallProgressPercent':round((t['p'] or 0)*100/(total*3)) if total else 0,'assemblyLaborMinutes':a['n'],'temporaryTransferLaborMinutes':x['n'],'totalLaborMinutes':a['n']+x['n'],'generatedAt':now()}
    @app.get('/api/v1/workshop/machine-progress')
    def machine(user=Depends(current_user),page:int=1,pageSize:int=20):
        if user['role'] not in ('WORKSHOP_SUPERVISOR','ADMIN'): raise HTTPException(403,'无统计权限')
        c=db(); rows=c.execute('SELECT device_id,device_no,count(*) taskCount,sum(status="COMPLETED") completedTaskCount,round(sum(progress_stage)*100.0/(count(*)*3)) progressPercent FROM assembly_tasks GROUP BY device_id,device_no LIMIT ? OFFSET ?',(pageSize,(page-1)*pageSize)).fetchall(); c.close(); return {'items':[dict(r) for r in rows],'page':page,'pageSize':pageSize}
