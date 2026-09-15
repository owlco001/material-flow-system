from fastapi import HTTPException, Depends, Query, Request, Header
import json, uuid

def register(app, db, now, current_user, audit_event, api_error=None):
    def forbidden(message='无装配操作权限'):
        if api_error: raise api_error(403, 'ASSEMBLY_TASK_FORBIDDEN', message)
        raise HTTPException(403, message)
    def actor(u):
        if u['role'] != 'ASSEMBLER': forbidden()
    def active_member(c, tid, uid):
        return c.execute("""SELECT 1 FROM assembly_task_members WHERE task_id=? AND assembler_id=? AND removed_at IS NULL
                           UNION ALL SELECT 1 FROM assembly_tasks WHERE id=? AND assigned_assembler_id=?""", (tid, uid, tid, uid)).fetchone()
    def task(c, tid, u):
        r=c.execute('SELECT * FROM assembly_tasks WHERE id=?',(tid,)).fetchone()
        if not r: raise HTTPException(404,'任务不存在')
        if u['role'] == 'ASSEMBLER' and not active_member(c, tid, u['id']): forbidden('只能操作本人任务')
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
        return {'id':r['id'],'orderNo':r['order_no'],'deviceId':r['device_id'],'deviceNo':r['device_no'],'assignedAssemblerId':r['assigned_assembler_id'],'status':r['status'],'progressStage':r['progress_stage'],'taskVersion':r['task_version'],'members':(r['members'] if isinstance(r, dict) and 'members' in r else []),'serverTime':now()}
    def task_members(c, tid):
        rows=c.execute("SELECT assembler_id,assignment_role,assigned_by,assigned_at,removed_at FROM assembly_task_members WHERE task_id=? AND removed_at IS NULL ORDER BY assigned_at,assembler_id",(tid,)).fetchall()
        out=[dict(r) for r in rows]
        legacy=c.execute("SELECT assigned_assembler_id FROM assembly_tasks WHERE id=? AND assigned_assembler_id IS NOT NULL",(tid,)).fetchone()
        if legacy and not any(x['assembler_id']==legacy['assigned_assembler_id'] for x in out):
            out.insert(0, {'assembler_id':legacy['assigned_assembler_id'],'assignment_role':'LEAD','assigned_by':None,'assigned_at':None,'removed_at':None})
        return out
    def finish(c, rid):
        t=now(); r=c.execute('SELECT started_at FROM labor_records WHERE id=?',(rid,)).fetchone(); secs=max(0, int(__import__('datetime').datetime.fromisoformat(t).timestamp()-__import__('datetime').datetime.fromisoformat(r['started_at']).timestamp()))
        c.execute("UPDATE labor_records SET status='COMPLETED',ended_at=?,duration_minutes=? WHERE id=?",(t,secs//60,rid))
    def bodycheck(b):
        if not isinstance(b,dict): raise HTTPException(422,'请求参数无效')
    def stage_conflict(code, message, trace_id):
        if api_error: raise api_error(409, code, message, trace_id=trace_id)
        raise HTTPException(409, message)
    def stage_operation(body, key, request):
        client = body.get('clientOperationId')
        if not isinstance(client, str): raise HTTPException(400, 'clientOperationId 必须是 UUID')
        try: uuid.UUID(client)
        except ValueError: raise HTTPException(400, 'clientOperationId 必须是 UUID') from None
        if not isinstance(key, str) or not key.strip() or key.strip().lower() != client.lower():
            if api_error: raise api_error(400, 'IDEMPOTENCY_KEY_MISMATCH', 'Idempotency-Key 与 clientOperationId 不一致')
            raise HTTPException(400, 'Idempotency-Key 与 clientOperationId 不一致')
        request_id = request.headers.get('X-Request-Id')
        try: uuid.UUID(request_id or '')
        except ValueError:
            if api_error: raise api_error(400, 'INVALID_REQUEST_ID', 'X-Request-Id 必须是合法 UUID')
            raise HTTPException(400, 'X-Request-Id 必须是合法 UUID')
        return client, request_id
    def stage_result(r):
        return {'taskId': r['task_id'], 'stageNo': r['stage_no'], 'status': r['status'], 'version': r['version'], 'startedAt': r['started_at'], 'completedAt': r['completed_at'], 'reworkReason': r['rework_reason'], 'serverTime': now()}
    def mutate_stage(tid, stage_no, body, request, user, key, action):
        actor(user); bodycheck(body)
        if stage_no not in (1, 2, 3): raise HTTPException(422, 'stage_no 必须为 1..3')
        opid, request_id = stage_operation(body, key, request)
        c=db()
        try:
            c.execute('BEGIN IMMEDIATE')
            r=task(c, tid, user)
            existing=c.execute('SELECT * FROM assembly_task_stages WHERE task_id=? AND stage_no=?',(tid,stage_no)).fetchone()
            if not existing:
                t=now(); c.execute("INSERT INTO assembly_task_stages(task_id,stage_no,status,version,updated_at) VALUES(?,?, 'NOT_STARTED',1,?)",(tid,stage_no,t)); existing=c.execute('SELECT * FROM assembly_task_stages WHERE task_id=? AND stage_no=?',(tid,stage_no)).fetchone()
            prior=c.execute('SELECT * FROM assembly_stage_operations WHERE client_operation_id=?',(opid,)).fetchone()
            payload=json.dumps(body,ensure_ascii=False,sort_keys=True)
            if prior:
                if prior['task_id'] != tid or prior['stage_no'] != stage_no or prior['action'] != action or prior['payload_json'] != payload:
                    stage_conflict('IDEMPOTENCY_PAYLOAD_MISMATCH', '相同幂等键的请求体不一致', request_id)
                out=json.loads(prior['result_json']); out['idempotent']=True; c.rollback(); return out
            expected=body.get('expectedVersion')
            if not isinstance(expected,int) or expected != existing['version']:
                stage_conflict('ASSEMBLY_STAGE_VERSION_CONFLICT', '阶段版本冲突', request_id)
            reason=body.get('reworkReason')
            valid={'start': existing['status'] in ('NOT_STARTED','REWORK_REQUIRED'), 'complete': existing['status']=='IN_PROGRESS', 'rework': existing['status'] in ('IN_PROGRESS','COMPLETED')}
            if not valid[action]: stage_conflict('ASSEMBLY_STAGE_STATE_CONFLICT', '阶段状态不允许此操作', request_id)
            if action=='rework' and (not isinstance(reason,str) or not 1 <= len(reason) <= 500): raise HTTPException(422, 'reworkReason 必填且长度为 1..500')
            t=now(); version=existing['version']+1
            status={'start':'IN_PROGRESS','complete':'COMPLETED','rework':'REWORK_REQUIRED'}[action]
            started=t if action=='start' else existing['started_at']; completed=t if action=='complete' else None
            c.execute('UPDATE assembly_task_stages SET status=?,version=?,started_at=?,completed_at=?,rework_reason=?,updated_at=? WHERE task_id=? AND stage_no=?',(status,version,started,completed,reason if action=='rework' else None,t,tid,stage_no))
            out=stage_result(c.execute('SELECT * FROM assembly_task_stages WHERE task_id=? AND stage_no=?',(tid,stage_no)).fetchone()); out['traceId']=request_id
            _event={'start':'ASSEMBLY_STAGE_STARTED','complete':'ASSEMBLY_STAGE_COMPLETED','rework':'ASSEMBLY_STAGE_REWORK_REQUIRED'}[action]
            audit_event(c, _event, tid, user, request_id, opid, stage_result(existing), out, request, entity_type='ASSEMBLY_TASK_STAGE')
            c.execute('INSERT INTO assembly_stage_operations VALUES(?,?,?,?,?,?,?)',(opid,tid,stage_no,action,payload,json.dumps(out,ensure_ascii=False),t)); c.commit(); return out
        except Exception:
            c.rollback(); raise
        finally: c.close()
    @app.post('/api/v1/assembly/tasks/{tid}/stages/{stage_no}/start')
    def stage_start(tid:str, stage_no:int, body:dict, request:Request, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        return mutate_stage(tid,stage_no,body,request,user,idempotency_key,'start')
    @app.post('/api/v1/assembly/tasks/{tid}/stages/{stage_no}/complete')
    def stage_complete(tid:str, stage_no:int, body:dict, request:Request, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        return mutate_stage(tid,stage_no,body,request,user,idempotency_key,'complete')
    @app.post('/api/v1/assembly/tasks/{tid}/stages/{stage_no}/rework')
    def stage_rework(tid:str, stage_no:int, body:dict, request:Request, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        return mutate_stage(tid,stage_no,body,request,user,idempotency_key,'rework')
    @app.get('/api/v1/assembly/tasks')
    def list_tasks(page:int=Query(1,ge=1), pageSize:int=Query(20,ge=1,le=100), deviceId:str|None=None, user=Depends(current_user)):
        c=db(); clauses=[]; args=[]
        if user['role']=='ASSEMBLER':
            clauses.append("(EXISTS (SELECT 1 FROM assembly_task_members m WHERE m.task_id=assembly_tasks.id AND m.assembler_id=? AND m.removed_at IS NULL) OR assigned_assembler_id=?)")
            args += [user['id'], user['id']]
        if deviceId: clauses.append('device_id=?'); args.append(deviceId)
        where=' AND '.join(clauses) or '1=1'
        total=c.execute(f'SELECT count(*) n FROM assembly_tasks WHERE {where}',args).fetchone()['n']; rows=c.execute(f'SELECT * FROM assembly_tasks WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?',args+[pageSize,(page-1)*pageSize]).fetchall()
        items=[]
        for r in rows:
            x=result_task(r); x['members']=task_members(c,r['id']); items.append(x)
        c.close(); return {'items':items,'page':page,'pageSize':pageSize,'total':total,'totalPages':(total+pageSize-1)//pageSize}

    def assignment_operation(body, key, request):
        client=body.get('clientOperationId')
        try: uuid.UUID(str(client))
        except (ValueError, TypeError): raise HTTPException(422,'clientOperationId 必须是 UUID')
        if key and key.lower()!=str(client).lower(): raise HTTPException(400,'Idempotency-Key 与 clientOperationId 不一致')
        rid=request.headers.get('X-Request-Id')
        try: uuid.UUID(rid or '')
        except ValueError: raise HTTPException(400,'X-Request-Id 必须是合法 UUID')
        return str(client),rid
    def assignment_result(c, tid, rid, idem=False):
        return {'taskId':tid,'members':task_members(c,tid),'traceId':rid,'idempotent':idem,'serverTime':now()}
    @app.post('/api/v1/assembly/tasks/{tid}/assignments')
    def assign(tid:str, body:dict, request:Request, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        if user['role'] not in ('ADMIN','WORKSHOP_SUPERVISOR'): raise HTTPException(403,'无任务分配权限')
        ids=body.get('assemblerIds')
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or len(set(ids))!=len(ids): raise HTTPException(422,'assemblerIds 必须为 1..20 个不同成员')
        opid,rid=assignment_operation(body,idempotency_key,request); c=db()
        try:
            c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT id FROM assembly_tasks WHERE id=?',(tid,)).fetchone()
            if not t: raise HTTPException(404,'任务不存在')
            prior=c.execute('SELECT * FROM assembly_assignment_operations WHERE client_operation_id=?',(opid,)).fetchone(); payload=json.dumps(body,ensure_ascii=False,sort_keys=True)
            if prior:
                if prior['payload_json']!=payload: raise HTTPException(409,'相同幂等键的请求体不一致')
                out=json.loads(prior['result_json']); out['idempotent']=True; c.rollback(); return out
            for aid in ids:
                if not c.execute("SELECT 1 FROM users WHERE id=? AND role='ASSEMBLER' AND active=1",(aid,)).fetchone(): raise HTTPException(422,'成员必须是启用的 ASSEMBLER')
            for aid in ids:
                c.execute("INSERT INTO assembly_task_members(task_id,assembler_id,assignment_role,assigned_by,assigned_at,removed_at) VALUES(?,?,?,?,?,NULL) ON CONFLICT(task_id,assembler_id) DO UPDATE SET assignment_role=excluded.assignment_role,assigned_by=excluded.assigned_by,assigned_at=excluded.assigned_at,removed_at=NULL",(tid,aid,'LEAD' if aid==ids[0] else 'MEMBER',user['id'],now()))
            out=assignment_result(c,tid,rid); audit_event(c,'ASSEMBLY_TASK_ASSIGNED',tid,user,rid,opid,{},out,request,entity_type='ASSEMBLY_TASK'); c.execute('INSERT INTO assembly_assignment_operations VALUES(?,?,?,?,?,?)',(opid,tid,'ASSIGN',payload,json.dumps(out,ensure_ascii=False),now())); c.commit(); return out
        except Exception: c.rollback(); raise
        finally: c.close()
    @app.delete('/api/v1/assembly/tasks/{tid}/assignments/{assembler_id}')
    def unassign(tid:str, assembler_id:str, request:Request, body:dict, user=Depends(current_user), idempotency_key:str|None=Header(None,alias='Idempotency-Key')):
        if user['role'] not in ('ADMIN','WORKSHOP_SUPERVISOR'): raise HTTPException(403,'无任务分配权限')
        opid,rid=assignment_operation(body,idempotency_key,request); c=db()
        try:
            c.execute('BEGIN IMMEDIATE'); payload=json.dumps(body,ensure_ascii=False,sort_keys=True); prior=c.execute('SELECT * FROM assembly_assignment_operations WHERE client_operation_id=?',(opid,)).fetchone()
            if prior:
                if prior['payload_json']!=payload: raise HTTPException(409,'相同幂等键的请求体不一致')
                out=json.loads(prior['result_json']); out['idempotent']=True; c.rollback(); return out
            if not c.execute('SELECT 1 FROM assembly_tasks WHERE id=?',(tid,)).fetchone(): raise HTTPException(404,'任务不存在')
            c.execute('UPDATE assembly_task_members SET removed_at=? WHERE task_id=? AND assembler_id=? AND removed_at IS NULL',(now(),tid,assembler_id))
            c.execute('UPDATE assembly_tasks SET assigned_assembler_id=NULL WHERE id=? AND assigned_assembler_id=?',(tid,assembler_id))
            out=assignment_result(c,tid,rid); audit_event(c,'ASSEMBLY_TASK_MEMBER_REMOVED',tid,user,rid,opid,{},out,request,entity_type='ASSEMBLY_TASK'); c.execute('INSERT INTO assembly_assignment_operations VALUES(?,?,?,?,?,?)',(opid,tid,'REMOVE',payload,json.dumps(out,ensure_ascii=False),now())); c.commit(); return out
        except Exception: c.rollback(); raise
        finally: c.close()
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
