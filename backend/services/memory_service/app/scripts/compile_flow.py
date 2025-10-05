import os
import shutil
import argparse

def main():
    parser = argparse.ArgumentParser(description="Dummy compiler: JSON → .pb")
    parser.add_argument("input", help="Path to input JSON file")
    parser.add_argument("output", help="Path to output PB file")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    # Dummy compiler: hanya copy file json → pb
    shutil.copy(args.input, args.output)
    print(f"✅ Dummy flow compiled to {args.output}")

if __name__ == "__main__":
    main()
