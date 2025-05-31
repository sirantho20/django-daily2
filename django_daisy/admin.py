import logging

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt

from django_daisy._helpers import ASSET_PATH
from django_daisy.module_settings import DAISY_SETTINGS
from django_daisy.module_settings import APPS_REORDER

logger = logging.getLogger(__name__)

ADMIN_APP_CONFIG = getattr(settings, 'ADMIN_APP_CONFIG', {})

# Remove default form fields for specific date and time fields
# admin.options.FORMFIELD_FOR_DBFIELD_DEFAULTS.pop(models.DateTimeField, None)
# admin.options.FORMFIELD_FOR_DBFIELD_DEFAULTS.pop(models.DateField, None)
# admin.options.FORMFIELD_FOR_DBFIELD_DEFAULTS.pop(models.TimeField, None)


class DaisyAdminSite(admin.AdminSite):
    password_change_template = "admin/registration/password_change_form.html"
    password_change_done_template = "admin/registration/password_change_done.html"
    site_title = DAISY_SETTINGS.get("SITE_TITLE", "django admin")
    site_header = DAISY_SETTINGS.get("SITE_HEADER", "Administration")
    index_title = DAISY_SETTINGS.get("SITE_HEADER", "hi, welcome to your dashboard")
    logo = DAISY_SETTINGS.get(
        "SITE_LOGO", f"{ASSET_PATH}admin/img/daisyui-logomark.svg"
    )

    def get_urls(self):
        urls = [
            path("json-editor-upload-handler/", self.admin_view(self.upload_file), name='json-editor-upload-handler')
        ]
        return urls + super().get_urls()

    # def get_log_entries(self, request):
    #     from django.contrib.admin.models import LogEntry

    #     return LogEntry.objects.select_related("content_type", "user")

    # def index(self, request, extra_context=None):
    #     """
    #     Display the main admin index page, which lists all of the installed
    #     apps that have been registered in this site.
    #     """

    #     logentry_changelist_url = reverse("admin:admin_logentry_changelist")

    #     app_list = self.get_app_list(request)

    #     context = {
    #         **self.each_context(request),
    #         "latest_history": self.get_log_entries(request)[:15],
    #         "title": self.index_title,
    #         "app_list": app_list,
    #         "logentry_changelist_url": logentry_changelist_url,
    #         **(extra_context or {}),
    #     }

    #     request.current_app = self.name
    #     return render(request, self.index_template or "admin/index.html", context)

    # Re-written to support model level reordering with priority
    def get_app_list(self, request, app_label=None):
        """
        Return a list of all registered apps, filtered and ordered according
        to ADMIN_APP_CONFIG in settings.py, enforcing any specified perms.
        """
        # 1. First grab Django's default app_list (each element is a dict).
        base_apps = super(DaisyAdminSite, self).get_app_list(request, app_label)
        # raise ValueError(base_apps)
        final_apps = []
        reorder_config = ADMIN_APP_CONFIG

        for app in base_apps:
            # Each `app` is a dict with keys like:
            #   'name', 'app_label', 'app_url', 'has_module_perms', 'models' (list of dicts), etc.
            label = app.get('app_label')
            if not label:
                # Something unexpected; skip to avoid indices-must-be-integer errors
                continue

            cfg = reorder_config.get(label, {})

            # A) Enforce app-level permission if defined
            app_perm = cfg.get('perm')
            if app_perm and not request.user.has_perm(app_perm):
                continue

            # B) Hide flag at the app level
            if cfg.get('hide', False):
                continue

            # C) Filter models by individual model permissions
            original_models = app.get('models', [])
            model_cfgs = cfg.get('models', {})
            explicit_order = cfg.get('order')  # e.g. ['User','Group'] or None
            visible_models = []

            for m in original_models:
                # Each `m` is a dict, e.g. {'name': 'User', 'object_name': 'User', ...}
                model_obj_name = m.get('object_name')
                if not model_obj_name:
                    continue

                # Determine which permission is required for this model:
                # - If model_cfgs has an entry, use that model-level 'perm'
                # - Otherwise default to "<app_label>.view_<modelname.lower()>"
                perm_for_model = model_cfgs.get(model_obj_name, {}).get(
                    'perm',
                    f"{label}.view_{model_obj_name.lower()}"
                )
                if not request.user.has_perm(perm_for_model):
                    continue

                # D) If the config defines a custom display name for this model, apply it:
                custom_model_name = model_cfgs.get(model_obj_name, {}).get('name')
                if custom_model_name:
                    m['name'] = custom_model_name

                visible_models.append(m)

            # If no models are left visible, skip this entire app
            if not visible_models:
                continue

            # E) If an explicit model ordering was given, apply it
            if explicit_order:
                visible_models.sort(
                    key=lambda x: (
                        explicit_order.index(x['object_name'])
                        if x['object_name'] in explicit_order
                        else len(explicit_order)
                    )
                )
            else:
                # Fallback: sort alphabetically by model display name
                visible_models.sort(key=lambda x: x['name'])

            # Replace app['models'] with our filtered + ordered list
            app['models'] = visible_models

            # G) Apply any display overrides on the app itself:
            for attr in ('name', 'icon', 'divider_title', 'priority'):
                if attr in cfg:
                    app[attr] = cfg[attr]

            final_apps.append(app)

        # H) Finally, sort the apps by descending 'priority' (default = 0)
        final_apps.sort(key=lambda a: a.get('priority', 0), reverse=True)

        return final_apps
    
    def _build_app_dict(self, request, label=None):
        """
        Builds and modifies the app dictionary to include icons and app grouping logic.
        """
        app_dict = super()._build_app_dict(request, label)

        if label:
            return app_dict

        modified_app_dict = app_dict.copy()
        override_apps_config = DAISY_SETTINGS.get("APPS_REORDER", {})

        for app_label, app_info in app_dict.items():
            # Add icon and divider title to each app
            app_info["icon"] = getattr(apps.get_app_config(app_label), "icon", "")
            app_info["divider_title"] = getattr(
                apps.get_app_config(app_label), "divider_title", ""
            )

            if app_label in override_apps_config:
                app_info.update(override_apps_config[app_label])

        return modified_app_dict

    def each_context(self, request):
        context = super().each_context(request)
        try:
            change_language_url = reverse("set_language")
        except:
            change_language_url = None

        daisy_settings = DAISY_SETTINGS.copy()
        # daisy_settings.pop("APPS_REORDER", None)

        return {
            **context,
            **daisy_settings,
            "change_language_url": change_language_url,
            "logo": self.get_logo(request),
            "can_delete_popup": "",
            "use_i18n": getattr(settings, "USE_I18N", False),
        }

    def get_logo(self, request):
        return self.logo

    @csrf_exempt
    def upload_file(self, request):
        if request.method != "POST":
            return HttpResponse(
                "Invalid request method", status=405, content_type="text/plain"
            )

        if "file" not in request.FILES:
            return HttpResponse(
                "No file uploaded", status=400, content_type="text/plain"
            )

        file = request.FILES["file"]

        try:
            fs = FileSystemStorage()
            filename = fs.save(file.name, file)
            url = request.build_absolute_uri(fs.url(filename))
            return HttpResponse(url, content_type="text/plain")

        except Exception as e:
            # Log the error for debugging purposes
            logger.error("Error saving file: %s", e)
            # Return a generic error message to the client
            return HttpResponse(
                "Failed to save file", status=500, content_type="text/plain"
            )
