const scheduleItemInitialState = () => ({
    cron: '0 0 0 0 0',
    cron_radio: 'custom',
    active: true,
    name: '',
    id: null,
    test_params: [],
    errors: {}
})

const ScheduleItem = {
    props: [...Object.keys(scheduleItemInitialState()), 'schedule_id'],
    emits: [
        ...Object.keys(scheduleItemInitialState()).filter(item => !['id', 'errors'].includes(item)).map(item => `update:${item}`),
        'delete'
    ],
    delimiters: ['[[', ']]'],
    data() {
        return {
            periods: ['daily', 'weekly', 'monthly', 'yearly', 'custom'],
            test_params_open: false,
        }
    },
    mounted() {
        this.test_params_table.ready(() => {
            this.test_params_table.bootstrapTable()
            this.test_params_table.bootstrapTable('load', this.test_params)
            this.test_params_table.on('all.bs.table', (e) => {
                this.$emit('update:test_params', this.test_params_table.bootstrapTable('getData'))
                this.test_params_table.find('.selectpicker').selectpicker('render')
            })
            this.test_params_table.find('.selectpicker').selectpicker('render')
        })
    },
    methods: {
        handleInputChange(e) {
            this.$emit('update:cron_radio', e.target.value)
            const today = new Date()
            switch (e.target.value) {
                case 'daily':
                    this.$emit('update:cron',
                        `${today.getMinutes()} ${today.getHours()} * * *`)
                    break
                case 'weekly':
                    this.$emit('update:cron',
                        `${today.getMinutes()} ${today.getHours()} * * ${today.getDay()}`)
                    break
                case 'monthly':
                    this.$emit('update:cron',
                        `${today.getMinutes()} ${today.getHours()} ${today.getDate()} * *`)
                    break
                case 'yearly':
                    this.$emit('update:cron',
                        `${today.getMinutes()} ${today.getHours()} ${today.getDate()} ${today.getMonth() + 1} *`)
                    break
                default:
            }
        },
    },
    watch: {
        errors(newE, oldE) {
            if (!!newE?.test_params) {
                const [row, col_name, ..._] = newE.test_params.loc
                // const get_col_by_name = name => $(`#security_test_params thead th[data-field=${name}]`).index()
                const get_col_by_name = name => $(`#${this.test_params_id} thead th[data-field=${name}]`).index()
                $(`#${this.test_params_id} tr[data-index=${row}] td:nth-child(${get_col_by_name(col_name) + 1}) input`)
                    .addClass('is-invalid')
                    .next('div.invalid-tooltip-custom')
                    .text(newE.test_params.msg)
            } else {
                $(`#${this.test_params_id}`).removeClass('is-invalid')
            }


        }
    },
    computed: {
        test_params_id() {
            return `schedule_test_params_${this.schedule_id}`
        },
        test_params_table() {
            return $(`#${this.test_params_id} table.params-table`)
        },

    },
    template: `
        <div class="mb-3 card card-x mx-auto px-24 pt-20 pb-24">
            <div class="d-flex mb-3">
                <p class="font-h5 font-bold flex-grow-1">Set schedule</p>
                <button type="button" class="btn btn-default btn-xs btn-table btn-icon__xs mr-2"
                    @click="$emit('delete', schedule_id)"
                >
                    <i class="icon__18x18 icon-delete"></i>
                </button>
                <label class="custom-toggle">
                    <input type="checkbox"
                        @change="$emit('update:active', $event.target.checked)"
                        :checked="active"
                    >
                    <span class="custom-toggle_slider round"></span>
                </label>
            </div>
                <div>
                    <div class="d-grid grid-column-2 gap-50">
                        <div>
                            <label class="w-100">
                                <p class="font-h5 font-semibold mb-2">Schedule name</p>
                                <input class="form-control form-control-alternative" type="text"
                                   placeholder="Schedule name"
                                   :value="name"
                                   @change="$emit('update:name', $event.target.value)"
                                   :class="{ 'is-invalid': errors?.name }"
                                >
                                <div class="invalid-feedback">[[ errors?.name?.msg ]]</div>
                            </label>
                        </div>
                        <div>
                            <p class="font-h5 font-semibold">Schedule</p>
                            <div>
                                <div class="d-flex my-3">
                                    <div class="d-flex" style="margin-right: 24px"
                                        v-for="t in periods">
                                        <input class="mx-2 custom-radio" 
                                            type="radio" 
                                            :value="t"
                                            :id="'cron_radio_' + t"
                                            :name="'cron_radio_' + schedule_id"
                                            :checked="cron_radio === t"
                                            @change="handleInputChange"
                                        >
                                        <label class="mb-0 w-100 d-flex align-items-center" :for="'cron_radio_' + t">
                                            <span class="w-100 d-inline-block font-h5 font-weight-400 text-capitalize">[[ t ]]</span>
                                        </label>
                                    </div>
                                </div>
                                <p class="font-h6 font-semibold mb-1 d-flex">Crontab expression <i class="icon__16x16 icon-info__16 ml-1"></i></p>
                                <div class="input-group d-flex justify-content-around m-auto">
                                    <input class="form-control form-control-alternative text-center" type="text"
                                       placeholder="* * * * *"
                                       style="border-top-right-radius: 4px; border-bottom-right-radius: 4px"
                                       :value="cron"
                                       :disabled="cron_radio !== 'custom'"
                                       @change="$emit('update:cron', $event.target.value)"
                                       :class="{ 'is-invalid': errors?.cron }"
                                    >
                                    <div class="invalid-feedback">[[ errors?.cron?.msg ]]</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="mt-3">
                        <div class="mb-1"
                             style="cursor: pointer"
                             :data-target="'#' + test_params_id"
                        >
                            <p class="font-h5 font-uppercase font-semibold">
                                Test parameters
                            </p>
                            <p class="font-h6 font-weight-400">Specify parameters for test runs</p>
                        </div>
                        <div class="section-h-auto" :id="test_params_id">
                            <slot></slot>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
}

const SchedulingApp = {
    delimiters: ['[[', ']]'],
    props: ['instance_name', 'params_table'],
    components: {
        'schedule-item': ScheduleItem
    },
    data() {
        return {
            schedules_items: [],
            errors: {}
        }
    },
    mounted() {
        new SectionDataProvider('scheduling', {
            get: () => this.schedules_items,
            set: values => {
                this.schedules_items = values.map(item => ({...scheduleItemInitialState(), ...item}))
            },
            clear: () => this.schedules_items = [],
            setError: data => {
                const [_, index, field, ...rest] = data.loc

                if (this.errors[index]) {
                    this.errors[index][field] = {loc: rest, msg: data.msg}
                } else {
                    this.errors[index] = {[field]: {loc: rest, msg: data.msg}}
                }
            },
            clearErrors: () => this.errors = {}
        }).register()
    },
    methods: {
        handleDeleteItem(schedule_id) {
            this.schedules_items.splice(schedule_id, 1)
        },
        handleAddItem() {
            this.schedules_items.push(scheduleItemInitialState())
        }
    },
    template: `
        <div class="modal-body">
            <div class="row">
                <div class="col mb-3">
                    <p class="font-h5 font-bold font-uppercase">Scheduling</p>
                    <p class="font-h6 font-weight-400">You can create several schedules of this test with different parameters</p>
                </div>
        
                <schedule-item
                        v-for="(item, index) in schedules_items"
                        :key="index"
                        v-model:name="item.name"
                        v-model:active="item.active"
                        v-model:cron="item.cron"
                        v-model:cron_radio="item.cron_radio"
                        v-model:test_params="item.test_params"
                        :schedule_id="index"
                        @delete="handleDeleteItem"
                        :errors="errors[index]"
                >
                    <div v-html="params_table"></div>
                </schedule-item>
            </div>
            <button type="button" class="btn btn-sm btn-secondary"
                    @click.prevent="handleAddItem"
            >
                <span class="fa fa-plus mr-2"></span> Add schedule
            </button>
        </div>
    `
}


register_component('scheduling', SchedulingApp)
