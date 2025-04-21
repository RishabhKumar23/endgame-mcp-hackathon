import requests
import certifi

url = "https://data.dev.masalabs.ai"

# Option 1: Use macOS Keychain (works if certificate is trusted in Keychain)
response = requests.get(url)

# Option 2: Use certifi's bundle (if you added the certificate manually)
response = requests.get(url, verify=certifi.where())

print(response.status_code)