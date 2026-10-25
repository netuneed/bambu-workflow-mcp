import argparse
from .config import Config
from .workflow import Workflow

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--job",required=True)
    parser.add_argument("--action",choices=["prepare","dispatch","reconcile"],required=True)
    args=parser.parse_args()
    config=Config(args.config)
    try:
        Workflow(config).run_worker(args.job,args.action)
    except Exception as exc:
        print(config.redact(str(exc)),flush=True)
        raise SystemExit(1)

if __name__ == "__main__": main()
