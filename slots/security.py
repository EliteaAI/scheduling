from pylon.core.tools import web, log


class Slot:
    @web.slot('security_scheduling_test_create_content')
    def test_create_content(self, context, slot, payload):
        with context.app.app_context():
            return self.descriptor.render_template(
                'security/test_create_content.html',
            )

    @web.slot('security_scheduling_test_creat_scripts')
    def test_create_scripts(self, context, slot, payload):
        with context.app.app_context():
            return self.descriptor.render_template(
                'security/test_create_scripts.html',
            )
