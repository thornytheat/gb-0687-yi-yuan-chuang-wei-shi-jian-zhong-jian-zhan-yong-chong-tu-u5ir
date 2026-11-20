from datetime import datetime, timedelta
from app.normalize import instant, number
from app.errors import DomainError
from app.modules.m2_mapping.rules import BEDS
from app.reasons import REASONS

ALL_BEDS = sorted(BEDS)


def _as_datetime(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


def parse_params(params):
    shift_at = instant(params['shift_time'])
    raw_beds = [x.strip() for x in str(params['isolation_beds']).split(',') if x.strip()]
    unknown = [x for x in raw_beds if x not in BEDS]
    if unknown:
        raise DomainError('UNMAPPED', f'未登记的隔离床号: {",".join(unknown)}')
    isolation_beds = sorted(set(raw_beds))
    reserve = number(params['suspect_reserve'])
    critical = number(params['critical_pending'])
    for name, value in (('suspect_reserve', reserve), ('critical_pending', critical)):
        if value < 0 or value != value.to_integral():
            raise DomainError('RANGE', f'{name}须为非负整数')
    if reserve > len(isolation_beds):
        raise DomainError('LIMIT', '疑似保留数不能超过隔离床总数')
    return shift_at, isolation_beds, int(reserve), int(critical)


def bed_occupants(intervals, shift_at):
    """半开区间 [start,end)：start<=shift_at<end 才算班次时点占用。"""
    occupants = {}
    for item in intervals:
        start = _as_datetime(item['start']); end = _as_datetime(item['end'])
        if start <= shift_at < end:
            occupants.setdefault(item['bed'], []).append({**item, 'start': start, 'end': end})
    return occupants


def _bed_reason(bed, occupied, isolation, reserved):
    if occupied:
        return 'OCCUPIED'
    if reserved:
        return 'ISOLATION_RESERVED'
    if isolation:
        return 'ISOLATION_READY'
    return 'GENERAL_FREE'


def build_board(intervals, params):
    shift_at, isolation_beds, reserve_n, critical_n = parse_params(params)
    occupants = bed_occupants(intervals, shift_at)
    free_isolation = [b for b in isolation_beds if b not in occupants]
    reserved = set(free_isolation[:reserve_n])
    board = []
    for bed in ALL_BEDS:
        items = occupants.get(bed, [])
        is_isolation = bed in isolation_beds
        is_reserved = bed in reserved
        occupied = bool(items)
        code = _bed_reason(bed, occupied, is_isolation, is_reserved)
        admissible = code in ('GENERAL_FREE', 'ISOLATION_READY')
        basis = [{
            'patient': x['patient'], 'bed': x['bed'], 'ward': x['ward'],
            'start': x['start'].isoformat(), 'end': x['end'].isoformat(),
            'open': x['open'], 'source': x['source'],
        } for x in sorted(items, key=lambda x: (x['start'], x['patient']))]
        board.append({
            'bed': bed, 'ward': BEDS[bed], 'isolation': is_isolation,
            'occupied': occupied, 'reserved': is_reserved,
            'admissible': admissible,
            # 普通收治视角：专用隔离床永远不可作为普通空床排床
            'general_admissible': (not is_isolation) and (not occupied),
            'code': code,
            'reason': REASONS[code], 'basis': basis,
        })
    general_free = sum(1 for x in board if x['code'] == 'GENERAL_FREE')
    isolation_free = len(free_isolation)
    general_capacity = general_free
    critical_admitted = min(critical_n, general_capacity)
    if critical_admitted >= critical_n and critical_n > 0:
        decision = '可立即收治'
    elif critical_n == 0:
        decision = '本班无重症待收'
    else:
        decision = '暂缓普通收治'
    reserve_margin = len(reserved)
    reserve_short = reserve_n - reserve_margin
    if reserve_short > 0:
        decision = '隔离余量不足'
    summary = (
        f'班次时点 {shift_at.isoformat()}：普通可收治床 {general_capacity} 张，'
        f'待收重症 {critical_n} 人，结论「{decision}」（本次可收 {critical_admitted} 人）；'
        f'专用隔离床 {len(isolation_beds)} 张中空闲 {isolation_free} 张，'
        f'为途中疑似患者保留 {reserve_margin} 张，隔离余量 {reserve_margin} 张'
        + (f'，缺口 {reserve_short} 张' if reserve_short else '')
        + '。专用隔离床不计入普通可收治床数。'
    )
    plan = {
        'shift_time': shift_at.isoformat(),
        'isolation_beds': isolation_beds,
        'suspect_reserve_requested': reserve_n,
        'critical_pending': critical_n,
        'bed_board': board,
        'counts': {
            'total_beds': len(ALL_BEDS),
            'isolation_beds': len(isolation_beds),
            'isolation_free': isolation_free,
            'occupied_beds': sum(1 for x in board if x['occupied']),
            'general_admissible': general_capacity,
            'reserve_margin': reserve_margin,
            'reserve_shortage': reserve_short,
            'critical_admitted': critical_admitted,
        },
        'decision': decision,
        'summary': summary,
        'reserve_policy': (
            f'保留的 {reserve_margin} 张隔离床仅用于途中疑似患者，普通重症不得占用；'
            '无普通空床时重症暂缓收治，隔离余量不下调。'
        ),
    }
    return plan


def _late_quarantine_rows(records, quarantine, late_minutes):
    """隔离区中时间可解析的迟到行：与采信行合并按原始行序扫水位后判定。"""
    timeline = [(r['_line'], r['time'], False, None) for r in records]
    for row in quarantine:
        raw = row.get('raw') or {}
        text_time = raw.get('time')
        if not text_time:
            continue
        try:
            when = instant(text_time)
        except DomainError:
            continue
        timeline.append((row.get('line'), when, True,
                         {'_line': row.get('line'), 'id': row.get('key'), 'time': when,
                          'bed': raw.get('bed', ''), 'quarantined': True, 'code': row.get('code')}))
    watermark = None
    late = []
    threshold = timedelta(minutes=float(late_minutes))
    for line, when, is_quarantine, payload in sorted(timeline, key=lambda x: x[0] if isinstance(x[0], int) else 0):
        if is_quarantine and watermark is not None and when < watermark - threshold:
            late.append(payload)
        watermark = when if watermark is None else max(watermark, when)
    return late


def late_isolation(ctx, plan):
    """晚到的隔离床记录会使本班次旧结论失效。

    迟到语义沿用 M7：仅采信 M7 审计已按文件原始行序标记的迟到事件；
    另纳入隔离区中时间可解析、且满足同一迟到阈值的隔离床记录（同样按行序判定）。
    """
    isolation_beds = set(plan['isolation_beds'])
    late_minutes = ctx['params']['late_minutes']
    triggers = []
    late_ids = {x['id'] for x in ctx.get('audit', {}).get('late', [])}
    for row in ctx['records']:
        if row['id'] in late_ids and row.get('bed') in isolation_beds:
            triggers.append({
                'line': row.get('_line'), 'id': row.get('id'),
                'bed': row.get('bed'), 'time': row['time'].isoformat(),
                'quarantined': False, 'code': 'LATE',
            })
    for row in _late_quarantine_rows(ctx['records'], ctx['quarantine'], late_minutes):
        if row.get('bed') in isolation_beds:
            triggers.append({
                'line': row.get('_line'), 'id': row.get('id'),
                'bed': row.get('bed'), 'time': row['time'].isoformat(),
                'quarantined': True, 'code': row.get('code', 'LATE'),
            })
    triggers.sort(key=lambda t: (t['line'] or 0, t['id'] or ''))
    return triggers
