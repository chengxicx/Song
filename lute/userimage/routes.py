"""
User images routes.
"""

import os
from flask import Blueprint, send_from_directory, current_app

bp = Blueprint("userimages", __name__, url_prefix="/userimages")


@bp.route("/<int:lgid>/<path:f>", methods=["GET"])
def get_image(lgid, f):
    "Serve the image from the data/userimages directory."
    # env_config is the user-scoped proxy: in multi-user mode images
    # live under the current user's own userimages directory.
    directory = os.path.join(current_app.env_config.userimagespath, str(lgid))
    if not os.path.exists(os.path.join(directory, f)):
        return ""
    return send_from_directory(directory, f)
