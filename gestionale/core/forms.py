from django import forms


class FormBootstrap:
    """Mixin per i form: aggiunge ai widget le classi di Bootstrap 5. I campi si mostrano con `_campo.html`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            widget = campo.widget
            classe = ("form-check-input" if isinstance(widget, forms.CheckboxInput)
                      else "form-select" if isinstance(widget, forms.Select) else "form-control")
            widget.attrs.setdefault("class", classe)


class CampoData(forms.DateInput):
    """Selettore di data del browser: vuole e restituisce le date in ISO."""

    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%d")
