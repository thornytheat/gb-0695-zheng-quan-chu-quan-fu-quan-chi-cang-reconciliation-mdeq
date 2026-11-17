from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m4',__name__)
@bp.route('/api/m4', methods=['GET','POST'])
def module_4(): return jsonify(stage(4))
