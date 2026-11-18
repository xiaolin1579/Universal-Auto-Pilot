"""Persistent queue rotation and a discovery turn after a full upload cycle."""


class UploadSchedule:
    def __init__(self, store):
        self.store = store
        self.state = store.read().get('auto_upload_runtime', {}).get('scheduler', {})

    def ordered(self, jobs):
        cursor = self.state.get('last_job')
        index = next((i + 1 for i, job in enumerate(jobs) if job['id'] == cursor), 0)
        return jobs[index:] + jobs[:index]

    @property
    def discovery_first(self):
        return self.state.get('discovery_first') is True

    def attempted(self, job):
        def update(data):
            state = data.setdefault('auto_upload_runtime', {}).setdefault('scheduler', {})
            state['last_job'] = job['id']
        self.store.update(update)

    def finish(self, saturated):
        def update(data):
            state = data.setdefault('auto_upload_runtime', {}).setdefault('scheduler', {})
            state['discovery_first'] = saturated
        self.store.update(update)
