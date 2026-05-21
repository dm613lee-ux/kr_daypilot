import urllib.request
try:
    req = urllib.request.Request('https://api.github.com/repos/dm613lee-ux/kr_daypilot/actions/jobs/76866668715/logs', headers={'User-Agent': 'Mozilla/5.0'})
    response = urllib.request.urlopen(req)
    logs = response.read().decode('utf-8')
    print(logs[-3000:])
except Exception as e:
    print('Error:', e)
