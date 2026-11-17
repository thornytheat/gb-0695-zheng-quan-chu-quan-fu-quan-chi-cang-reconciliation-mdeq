from flask import Blueprint, jsonify
from app.api import stage
bp=Blueprint('m3',__name__)
@bp.route('/api/m3', methods=['GET','POST'])
def module_3(): return jsonify(stage(3))
