def get_predictions(nf_up, nf_down, df_up, df_down):
    pred_up = nf_up.predict(df=df_up)
    pred_down = nf_down.predict(df=df_down)

    pred_up.columns = ['symbol','ds','up_pred']
    pred_down.columns = ['symbol','ds','down_pred']

    merged = pred_up.merge(pred_down, on=['symbol','ds'])
    merged['direction'] = (merged['up_pred'] - merged['down_pred']).round(4)
    return merged
