import pandas as pd

DATA_PATH = "data/processed/all_pairs_merged.csv"

def load_data(horizon=5):
    df = pd.read_csv(DATA_PATH)
    df['time'] = pd.to_datetime(df['time'])
    df['ds'] = df['time']
    df = df.sort_values(['symbol', 'time'])

    up = df.groupby('symbol')['m15_close'].apply(lambda x: (x.shift(-horizon) - x).clip(lower=0)).reset_index(level=0, drop=True)
    down = df.groupby('symbol')['m15_close'].apply(lambda x: (x - x.shift(-horizon)).clip(lower=0)).reset_index(level=0, drop=True)

    df['up_move'] = up
    df['down_move'] = down
    df = df.dropna(subset=['up_move', 'down_move'])

    df_tail = df.groupby('symbol').tail(500).copy()
    df_tail['unique_id'] = df_tail['symbol']

    df_up = df_tail[['unique_id', 'ds', 'up_move']]
    df_down = df_tail[['unique_id', 'ds', 'down_move']]

    return df_up, df_down
