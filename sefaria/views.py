from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render_to_response
from django.contrib.auth import login, authenticate
from django.template import RequestContext
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm, PasswordResetForm
from emailusernames.forms import EmailUserCreationForm
from sefaria.util import *
from sefaria.forms import NewUserForm

def register(request):
    if request.user.is_authenticated():
        return HttpResponseRedirect("/login")

    next = request.REQUEST.get('next', '')

    if request.method == 'POST':
        form = NewUserForm(request.POST)
        if form.is_valid():
            new_user = form.save()
            user = authenticate(email=form.cleaned_data['email'],
                                password=form.cleaned_data['password1'])
            login(request, user)
            next = request.POST.get("next", "/") + "?welcome=to-sefaria"
            return HttpResponseRedirect(next)
    else:
        form = NewUserForm()

    return render_to_response("registration/register.html", 
                                {'form' : form, 'next': next}, 
                                RequestContext(request))


def accounts(request):
    return render_to_response("registration/accounts.html", 
                                {"createForm": UserCreationForm(),
                                "loginForm": AuthenticationForm() }, 
                                RequestContext(request))


def subscribe(request, email):
    if subscribe_to_announce(email):
        return jsonResponse({"status": "ok"})
    else:
        return jsonResponse({"error": "Something went wrong."})

