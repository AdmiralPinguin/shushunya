from neuralforecast import NeuralForecast

MODEL_UP = "models/nhits/up"
MODEL_DOWN = "models/nhits/down"

def load_models():
    nf_up = NeuralForecast.load(MODEL_UP)
    nf_down = NeuralForecast.load(MODEL_DOWN)
    return nf_up, nf_down
