from .sim import prices,Position,simulate,summary
def main():
 p=prices(); o=simulate(p,Position(92,108,10_000),dynamic=True); print(summary(o,10_000))
if __name__=="__main__":main()
