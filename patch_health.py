import re

with open('main.py', 'r') as f:
    content = f.read()

# Add last_earnings and next_research_trigger
# In health():
health_func = """@app.get("/health")
def health():
    \"\"\"Health-check — always returns 200 while the service is up.\"\"\"
    loop = _loop
    if loop is None:
        return {"status": "initialising"}
    
    # Calculate next research trigger
    next_trigger = None
    if hasattr(loop, 'research'):
        last_run = loop.research._last_run
        if last_run:
            from datetime import timedelta
            next_trigger = (last_run + timedelta(hours=loop.research._interval_hours)).isoformat()
    
    # Get last earnings from audit trail
    last_earnings = "0.00"
    if hasattr(loop, 'audit_trail') and loop.audit_trail:
        for entry in reversed(loop.audit_trail.entries()):
            if entry.kind == "task_executed" and entry.outcome.get("success"):
                last_earnings = entry.outcome.get("amount_earned", "0.00")
                break
                
    return {
        "status": "alive" if loop.debt_engine.alive else "dead",
        "life": loop.debt_engine.state.life_number,
        "debt": str(loop.debt_engine.debt),
        "survival_state": loop.state_machine.state.value,
        "last_earnings": last_earnings,
        "next_research_trigger": next_trigger,
    }"""

content = re.sub(r'@app\.get\("/health"\).*?(?=\n\n|\Z)', health_func, content, flags=re.DOTALL)
with open('main.py', 'w') as f:
    f.write(content)
