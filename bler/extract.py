import sys
import json

def main():
    if len(sys.argv) != 3:
        print("Usage: python extract.py <json_file> <key>")
        sys.exit(1)

    json_file = sys.argv[1]
    key = sys.argv[2]

    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print("Expected JSON root to be a list of objects")
        sys.exit(1)

    try:
        with open("output.txt", "w") as out:
            for item in data:
                if isinstance(item, dict) and key in item:
                    out.write(f"{item[key]}\n")
    except Exception as e:
        print(f"Error writing output: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()