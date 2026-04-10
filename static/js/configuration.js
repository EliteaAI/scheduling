const handle_schedule_change = async data => {
    console.log(data)
    const api_url = V.build_api_url('scheduling', 'schedules', {trailing_slash: true})
    const resp = await fetch(api_url + V.project_id, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    if (resp.ok) {
        showNotify('SUCCESS', 'Schedule changed')
    } else {
        showNotify('ERROR', resp.status === 403 ? 'Permission denied' : 'Edit error')
    }
    return resp
}

const handle_cron_change = async (data, row_index) => {
    const resp = await handle_schedule_change(data)
    if (!resp.ok) {
        if (resp.status === 400) {
            const error_data = await resp.json()
            console.error(error_data[0])
            showNotify('ERROR', error_data[0].msg)
        }
    } else {
        V.registered_components.table_schedules?.table_action(
            'updateRow',
            {index: row_index, row: {cron: data.cron, cron_edit: false}}
        )
    }
}

const handle_rpc_kwargs_change = async (data, row_index) => {
    try {
        data.rpc_kwargs = JSON.parse(data.rpc_kwargs)
    } catch (e) {
        console.error(e)
        showNotify('ERROR', 'Invalid JSON')
        return
    }

    const resp = await handle_schedule_change(data)
    if (!resp.ok) {
        if (resp.status === 400) {
            const error_data = await resp.json()
            console.error(error_data[0])
            showNotify('ERROR', error_data[0].msg)
        }
    } else {
        V.registered_components.table_schedules?.table_action(
            'updateRow',
            {index: row_index, row: {rpc_kwargs: data.rpc_kwargs, rpc_kwargs_edit: false}}
        )
    }
}

var tableFormatters = {
    scheduleActions: (value, row, index, field) => {
        return 'qwe'
    },
    last_run: (value, row, index, field) => {
        return value && new Date(value).toLocaleString()
    },
    rpc_kwargs: (value, row, index, field) => {
        if (row.rpc_kwargs_edit) {
            return `
            <div class="d-flex justify-content-between">
            <textarea class="form-control" rows="3">${JSON.stringify(value, null, 2)}</textarea>
                <div class="d-flex align-items-center">
                    <button class="btn btn-success__custom btn-xs btn-icon__xs ml-2" 
                        onclick="handle_rpc_kwargs_change({
                            rpc_kwargs: $(this.closest('td')).find('textarea').val(), 
                            id: ${row.id}
                        }, ${index})"
                    >
                        <i class="icon__16x16 icon-check__white"></i>
                        <i class="preview-loader__white hidden"></i>
                    </button>
                    <button class="btn btn-secondary btn-xs btn-icon__xs ml-2" 
                        onclick="V.registered_components.table_schedules?.table_action(
                            'updateRow', 
                            {index: ${index}, row: {rpc_kwargs_edit: false}}
                        )"
                    >
                        <i class="icon__16x16 icon-close__16"></i>
                    </button>
                </div>   
            </div>
        `
        }
        return `
            <div class="d-flex justify-content-between">
                <span>${JSON.stringify(value)}</span>
                <button type="button" class="btn btn-default btn-xs btn-table btn-icon__xs" 
                    onclick="V.registered_components.table_schedules?.table_action(
                        'updateRow', 
                        {index: ${index}, row: {rpc_kwargs_edit: true}}
                    )"
                >
                    <i class="icon__18x18 icon-edit"></i>
                </button>
            </div>
        `
    },
    rpc_func: (value, row, index, field) => {
        switch (value) {
            case 'check_rabbit_queues':
                return 'Check rabbit queues'
            case 'backend_performance_run_retention_check':
                return 'Check backend performance retention'
            case 'backend_performance_run_scheduled_test':
                return 'Run backend test'
            default:
                return value
        }
    },
    active: (value, row, index, field) => {
        return `
        <label class="custom-toggle">
            <input type="checkbox"
                onchange="handle_schedule_change({active: this.checked, id: ${row.id}})"
                ${value && 'checked'}
            >
            <span class="custom-toggle_slider round"></span>
        </label>
        `
    },
    cron: (value, row, index, field) => {
        if (row.cron_edit) {
            return `
            <div class="d-flex justify-content-between">
                <input class="form-control" value="${value}" />
                <div class="d-flex align-items-center">
                    <button class="btn btn-success__custom btn-xs btn-icon__xs ml-2" 
                        onclick="handle_cron_change({
                            cron: $(this.closest('td')).find('input').val(), 
                            id: ${row.id}
                        }, ${index})"
                    >
                        <i class="icon__16x16 icon-check__white"></i>
                        <i class="preview-loader__white hidden"></i>
                    </button>
                    <button class="btn btn-secondary btn-xs btn-icon__xs ml-2" 
                        onclick="V.registered_components.table_schedules?.table_action(
                            'updateRow', 
                            {index: ${index}, row: {cron_edit: false}}
                        )"
                    >
                        <i class="icon__16x16 icon-close__16"></i>
                    </button>
                </div>   
            </div>
        `
        }
        return `
            <div class="d-flex justify-content-between">
                <span>${value}</span>
                <button type="button" class="btn btn-default btn-xs btn-table btn-icon__xs" 
                    onclick="V.registered_components.table_schedules?.table_action(
                        'updateRow', 
                        {index: ${index}, row: {cron_edit: true}}
                    )"
                >
                    <i class="icon__18x18 icon-edit"></i>
                </button>
            </div>
        `
    }
}