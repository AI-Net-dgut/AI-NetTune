"""频谱已观测状态：瞬时越阈与连续确认分开，不评价未来预测效果。"""
from datetime import timedelta

def crossing_state(observations, min_bands, min_minutes):
    run=0;confirmed=False;previous=None;latest=None;count=0
    for timestamp,value in observations:
        if previous is None or timestamp-previous != timedelta(minutes=1):
            run=0
        count=int(value)
        run=run+1 if count>=min_bands else 0
        confirmed=confirmed or run>=min_minutes
        previous=latest=timestamp
    return {'confirmed':confirmed,'active':run>=min_minutes,
            'consecutiveMinutes':run,'latestCount':count,
            'observedAt':latest.strftime('%Y-%m-%d %H:%M:%S') if latest is not None else None}
