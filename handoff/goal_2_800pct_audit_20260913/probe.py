import ast,pathlib,sqlite3,hashlib,json
p=pathlib.Path("antigravity_workspace/memory/goals_registry.py")
s=p.read_text()
tree=ast.parse(s)
tree.body=[n for n in tree.body if isinstance(n,(ast.Import,ast.ImportFrom,ast.ClassDef,ast.FunctionDef)) or (isinstance(n,ast.Assign) and all(isinstance(t,ast.Name) and t.id in {"DEFAULT_GOALS_DB_PATH","VERDICT_COMPLETE","VERDICT_INCOMPLETE"} for t in n.targets))]
scope={"__file__":str(p.resolve())}
exec(compile(tree,str(p),"exec"),scope)
db=sqlite3.connect(":memory:")
class Proxy:
    def close(self): pass
    def __getattr__(self,k): return getattr(db,k)
    def __setattr__(self,k,v): setattr(db,k,v)
sqlite3.connect=lambda *a,**k:Proxy()
r=scope["GoalsRegistry"](":memory:")
out=[]
for i,same in enumerate([True,False]):
    g="isolated_probe_"+str(i)
    r.add_goal(g,"800% audit","No trades, no folds, no run artifacts","strategy_research",True,False)
    a=r.record_verification(g,"reviewer_a","COMPLETE_100",evidence="unsupported approval",evidence_hash="same")
    b=r.record_verification(g,"reviewer_b","COMPLETE_100",evidence="unsupported approval",evidence_hash="same" if same else "different")
    out.append({"case":"same_hash_missing_all_machine_evidence" if same else "different_hash_control","first":a,"second":b,"status":r.get_goal(g)["status"]})
assert out[0]["status"]=="COMPLETED"
assert out[1]["status"]=="ACTIVE"
print(json.dumps({"source_sha256":hashlib.sha256(s.encode()).hexdigest(),"isolation":"Only definitions/constants loaded via AST; SQLite redirected to memory; production DB unchanged","results":out},ensure_ascii=False,indent=2))
