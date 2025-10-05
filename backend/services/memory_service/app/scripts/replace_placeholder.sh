#!/bin/bash
DIR=$1
FROM=$2
TO=$3

find $DIR -type f \( -name "*.py" -o -name "*.sh" \) \
  -exec sed -i "s/$FROM/$TO/g" {} +

echo "✅ Replaced '$FROM' → '$TO' in $DIR"
