"""Run `python -m app.recluster` to preview; add --apply to merge pending duplicates."""
import json
import sys
from . import main as m
from .dedup import decision

def run(apply=False):
    m.init()
    with m.db() as c:
        if apply:
            import sqlite3
            backup = m.DATA / ('before-recluster-'+m.now().replace(':','-')+'.db')
            with sqlite3.connect(backup) as dst:
                c.backup(dst)
        touched=set()
        for log in c.execute("SELECT action,detail FROM audit WHERE actor != 'collector'"):
            data=json.loads(log['detail'])
            if log['action']=='edit':
                touched.add(data['before']['id'])
            elif log['action'] in ('merge','split'):
                touched.update([data['from'],data['to']])
            elif log['action']=='export':
                touched.update(data['ids'])
        rows=c.execute("SELECT a.* FROM articles a JOIN events e ON e.id=a.event_id WHERE e.status='pending' AND e.exported IS NULL ORDER BY a.id").fetchall()
        targets=[]
        moves=[]
        mapping={}
        for row in rows:
            if row['event_id'] in touched:
                continue
            source=mapping.get(row['event_id'],row['event_id'])
            for old in targets:
                target=mapping.get(old['event_id'],old['event_id'])
                if source==target:
                    continue
                semantic=bool(row['vector'] and old['vector'])
                score=m.similarity(json.loads(row['vector']),json.loads(old['vector'])) if semantic else m.lexical(row['title'],old['title'])
                allowed, reason=decision(row['title'],row['summary'],row['published'],old,score,semantic,.94)
                if allowed and (reason.startswith('主體') or reason.startswith('同一標題')):
                    members=[r for r in rows if mapping.get(r['event_id'],r['event_id']) in (source,target)]
                    from .dedup import conflict
                    if any(conflict(a['title']+' '+(a['summary'] or ''),b['title']+' '+(b['summary'] or '')) for a in members for b in members):
                        continue
                    moves.append({'from':source,'to':target,'score':round(score,4),'reason':reason})
                    if apply:
                        c.execute('UPDATE articles SET event_id=? WHERE event_id=?',(target,source))
                        c.execute('UPDATE events SET candidate=? WHERE candidate=?',(target,source))
                        c.execute('UPDATE events SET candidate=NULL WHERE id=? AND candidate=?',(target,target))
                        m.audit(c,'collector','recluster',moves[-1])
                    mapping.update({k:target for k,v in mapping.items() if v==source})
                    mapping[source]=target
                    break
            targets.append(row)
        return moves

if __name__=='__main__':
    print(json.dumps({'applied':'--apply' in sys.argv,'merges':run('--apply' in sys.argv)},ensure_ascii=False,indent=2))
