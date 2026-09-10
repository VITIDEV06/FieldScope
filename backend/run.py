import uvicorn


if __name__ == "__main__":
    # Puerto alineado con frontend/js/api.js.
    # reload=False evita inicializaciones dobles del modelo QVAC.
    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=False)
