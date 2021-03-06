import urllib
import os
import json
import time
import datetime
import sublime
from xml.sax.saxutils import escape

from .. import requests
from .. import util
from ..libs import auth

# https://github.com/xjsender/simple-salesforce/blob/master/simple_salesforce/login.py
def soap_login(settings, session_id_expired=False, timeout=10):
    if not session_id_expired:
        session = util.get_session_info(settings)
        try:
            # Force login again every two hours
            time_stamp = session.get("time_stamp")
            dt = datetime.datetime.strptime(time_stamp, "%Y-%m-%d %H:%M:%S")
            intervalDT = datetime.timedelta(minutes=settings["force_login_interval"])
            if (dt + intervalDT) >= datetime.datetime.now():
                return session
        except:
            pass

    login_soap_request_body = """<?xml version="1.0" encoding="utf-8" ?>
        <env:Envelope
            xmlns:xsd="http://www.w3.org/2001/XMLSchema"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
            <env:Body>
                <n1:login xmlns:n1="urn:partner.soap.sforce.com">
                    <n1:username>{username}</n1:username>
                    <n1:password>{password}</n1:password>
                </n1:login>
            </env:Body>
        </env:Envelope>
    """.format(
        username = settings["username"], 
        password = escape(settings["password"]) + settings["security_token"]
    )

    headers = {
        'content-type': 'text/xml',
        'charset': 'UTF-8',
        'SOAPAction': 'login'
    }

    try:
        response = requests.post(settings["soap_login_url"], login_soap_request_body, 
            verify=False, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        if "repeat_times" not in globals():
            globals()["repeat_times"] = 1
        else:
            globals()["repeat_times"] += 1

        if settings["debug_mode"]:
            print ("Login Exception: " + str(e))
            print ("repeat_times: " + str(globals()["repeat_times"]))

        if globals()["repeat_times"] <= 12:
            return soap_login(settings, True, timeout)

        result = {
            "Error Message":  "Network connection timeout",
            "success": False
        }
        return result

    # If request succeed, just clear repeat_times
    if "repeat_times" in globals():
        del globals()["repeat_times"]

    result = {}
    if response.status_code != 200:
        # Log the error message
        if settings["debug_mode"]:
            print (response.content)

        except_msg = util.getUniqueElementValueFromXmlString(response.content, 'sf:exceptionMessage')
        result["Error Message"] = except_msg
        result["success"] = False
        return result

    session_id = util.getUniqueElementValueFromXmlString(response.content, 'sessionId')
    server_url = util.getUniqueElementValueFromXmlString(response.content, 'serverUrl')
    instance_url = server_url[ : server_url.find('/services')]
    user_id = util.getUniqueElementValueFromXmlString(response.content, 'userId')

    result = {
        "project name": settings["default_project"]["project_name"],
        "session_id": session_id,
        "metadata_url": instance_url + "/services/Soap/m/%s.0" % settings["api_version"],
        "rest_url": instance_url + "/services/data/v%s.0" % settings["api_version"],
        "apex_url": instance_url + "/services/Soap/s/%s.0" % settings["api_version"],
        "partner_url": instance_url + "/services/Soap/u/%s.0" % settings["api_version"],
        "instance_url": instance_url,
        "user_id": user_id,
        "time_stamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time())),
        "headers": {
            "Authorization": "OAuth " + session_id,
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json"
        },
        "success": response.status_code < 399,
    }

    # If session is expired, just write session 
    # to .config/session.json
    util.add_config_history('session', result, settings)

    return result

from ..libs import server
sfdc_oauth_server = None

def start_server():
    global sfdc_oauth_server
    if sfdc_oauth_server is None:
        sfdc_oauth_server = server.Server()

def stop_server():
    global sfdc_oauth_server
    if sfdc_oauth_server is not None:
        sfdc_oauth_server.stop()
        sfdc_oauth_server = None

# Only support grant_type is authorization_code
def rest_login(settings, session_id_expired=False, timeout=10):
    session = util.get_session_info(settings)
    if not session_id_expired:
        try:
            # Force login again every two hours
            time_stamp = session.get("time_stamp")
            dt = datetime.datetime.strptime(time_stamp, "%Y-%m-%d %H:%M:%S")
            intervalDT = datetime.timedelta(minutes=settings["force_login_interval"])
            if (dt + intervalDT) >= datetime.datetime.now():
                return session
        except:
            pass

    # Get haoide default oAuth2 info
    app = sublime.load_settings("app.sublime-settings")
    oauth = auth.SalesforceOAuth2(
        app.get("client_id"),
        app.get("client_secret"),
        app.get("redirect_uri"),
        login_url=settings["login_url"]
    )

    # If refresh token is exist, just refresh token
    if session and session.get("refresh_token"):
        result = oauth.refresh_token(session.get("refresh_token"))

        # If succeed, 
        if result.get("access_token"):
            instance_url = result["instance_url"]
            result["project name"] = settings["default_project"]["project_name"]
            result["session_id"] = result["access_token"]
            result["metadata_url"] = instance_url + "/services/Soap/m/%s.0" % settings["api_version"]
            result["rest_url"] = instance_url + "/services/data/v%s.0" % settings["api_version"]
            result["apex_url"] = instance_url + "/services/Soap/s/%s.0" % settings["api_version"]
            result["partner_url"] = instance_url + "/services/Soap/u/%s.0" % settings["api_version"]
            result["instance_url"] = instance_url
            result["time_stamp"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time()))
            result["user_id"] = result["id"][-18:]
            result["headers"] = {
                "Authorization": "OAuth " + result["access_token"],
                "Content-Type": "application/json; charset=UTF-8",
                "Accept": "application/json"
            }
            result["success"] = True
            result["refresh_token"] = session.get("refresh_token")
            util.add_config_history('session', result, settings)
            return result
        else:
            if settings["debug_mode"]:
                print (result)
            
            # Remove refresh token and start oAuth2 login again
            result.pop('refresh_token', None)
            util.add_config_history('session', result, settings)
            return rest_login(settings, session_id_expired)

    # Start oAuth2 login process
    authorize_url = oauth.authorize_url(settings["username"])
    start_server()
    util.open_with_browser(authorize_url)

    # Return Message if not login, session expired or session invalid
    error_message = "Waiting for oAuth2 login finished"
    if session_id_expired:
        error_message = "Session invalid or expired, " + error_message

    return {
        "success": False,
        "error_message": error_message
    }