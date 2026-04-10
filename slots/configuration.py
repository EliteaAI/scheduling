from pylon.core.tools import web, log


class Slot:
    @web.slot('configuration_scheduling_content')
    def backend_test_create_content(self, context, slot, payload):
        with context.app.app_context():
            return self.descriptor.render_template(
                'configuration/content.html',
            )

    @web.slot('configuration_scheduling_scripts')
    def backend_test_create_scripts(self, context, slot, payload):
        with context.app.app_context():
            return self.descriptor.render_template(
                'configuration/scripts.html',
            )
