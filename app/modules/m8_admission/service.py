from .rules import build_board, late_isolation


def execute(ctx):
    plan = build_board(ctx['intervals'], ctx['params'])
    triggers = late_isolation(ctx, plan)
    plan['late_isolation_triggers'] = triggers
    plan['superseded_by_late'] = bool(triggers)
    ctx['admission'] = plan
    counts = plan['counts']
    ctx['stages'].append({
        'title': '班次收治', 'rows': plan['bed_board'],
        'metrics': {
            '班次结论': plan['decision'],
            '普通可收治': counts['general_admissible'],
            '隔离余量': counts['reserve_margin'],
            '晚到隔离记录': len(triggers),
        },
    })
    return ctx
