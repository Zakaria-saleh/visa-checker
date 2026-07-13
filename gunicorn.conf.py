   import multiprocessing
   
   # Timeout increased to handle long-running visa checks
   timeout = 120  # 2 minutes instead of 30 seconds
   workers = multiprocessing.cpu_count() * 2 + 1
   worker_class = 'sync'
   keepalive = 5
