from fastapi import FastAPI

app = FastAPI(title="upaya-jivika")


@app.get("/health")
def health():
    return {"status": "alive"}
