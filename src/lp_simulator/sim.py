from dataclasses import dataclass
import numpy as np
import pandas as pd
@dataclass
class Position:
    lower: float
    upper: float
    capital: float
    fee_rate: float=0.0005
    gas_usd: float=2.5

def prices(n=240, seed=3):
    rng=np.random.default_rng(seed); return 100*np.exp(np.cumsum(rng.normal(0,0.012,n)))

def simulate(prices, pos: Position, dynamic=False):
    prices=np.asarray(prices,float); value=[]; fees=[]; gas=[]; active=True; last=prices[0]
    for p in prices:
        now=pos.lower<=p<=pos.upper
        if dynamic and now != active:
            gas.append(pos.gas_usd); active=now
        else: gas.append(0.0)
        share=1.0 if now else 0.0
        fees.append(pos.capital*pos.fee_rate*abs(np.log(p/last))*100*share)
        value.append(pos.capital*(0.5*(p/last)+0.5)*share + pos.capital*(1-share))
        last=p
    out=pd.DataFrame({"price":prices,"gross_value":value,"fees":fees,"gas":gas})
    out["net_value"]=out.gross_value+out.fees.cumsum()-out.gas.cumsum()
    out["impermanent_loss_pct"]=100*(out.gross_value/(pos.capital*(out.price/prices[0]+1)/2)-1)
    out["active"]=out.price.between(pos.lower,pos.upper)
    return out

def summary(out, initial):
    return {"final_net_pnl":float(out.net_value.iloc[-1]-initial),"fee_income":float(out.fees.sum()),"gas_spend":float(out.gas.sum()),"time_in_range":float(out.active.mean())}
