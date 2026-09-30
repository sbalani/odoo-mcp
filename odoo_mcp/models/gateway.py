import logging
from datetime import date

from markupsafe import escape

from odoo import Command, models
from odoo.exceptions import AccessError, MissingError
from odoo.osv import expression

from ..policy import (CHAT_TARGETS, CHAT_TOOLS, CRM_TOOLS, READ_TOOLS, RESOURCES, TODO_TOOLS, crm_values,
                      integer, search_args, text_value, todo_values, validate_arguments)

_logger = logging.getLogger(__name__)


class McpGateway(models.AbstractModel):
    _name = 'odoo.mcp.gateway'
    _description = 'Restricted MCP tool dispatcher'

    def _check_member(self):
        if self.env.su or not self.env.user.has_group('odoo_mcp.group_mcp_user'):
            raise AccessError('MCP requires a non-superuser with MCP read access.')

    def _tools(self):
        self._check_member()
        result = list(READ_TOOLS)
        if self.env.user.has_group('odoo_mcp.group_mcp_crm_write'):
            result += CRM_TOOLS
        if self.env.user.has_group('odoo_mcp.group_mcp_chat_write'):
            result += CHAT_TOOLS
        if self.env.user.has_group('odoo_mcp.group_mcp_todo_write'):
            result += TODO_TOOLS
        return result

    def _record(self, model, record_id, operation='read'):
        record = self.env[model].browse(integer(record_id, 'id')).exists()
        if not record:
            raise MissingError('Record not found.')
        record.check_access(operation)
        return record

    def _todo(self, record_id, operation='write'):
        record = self._record('project.task', record_id, operation)
        if record.project_id or self.env.user not in record.user_ids:
            raise AccessError('Only your assigned personal To-dos are available.')
        return record

    def _target(self, args):
        target = args.get('target')
        if not isinstance(target, str) or target not in CHAT_TARGETS:
            raise ValueError('Unsupported message target')
        record = self._todo(args.get('id'), 'read') if target == 'todo' else self._record(CHAT_TARGETS[target], args.get('id'))
        if target == 'channel' and not record.is_member:
            raise AccessError('You must be a member of this Discuss channel.')
        if target == 'invoice' and record.move_type not in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt'):
            raise AccessError('Only invoices, bills, receipts and credit notes are available.')
        return record

    def _search(self, args):
        resource, model, fields, filters, limit, offset = search_args(args)
        scope = []
        if resource == 'todos':
            scope = [('project_id', '=', False), ('user_ids', 'in', [self.env.uid])]
        elif resource == 'channels':
            scope = [('is_member', '=', True)]
        elif resource == 'invoices':
            scope = [('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt'])]
        elif resource == 'invoice_lines':
            scope = [('move_id.move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund', 'out_receipt', 'in_receipt']), ('display_type', '=', 'product')]
        elif resource == 'crm_activities':
            # mail.activity's own access checks still apply to the linked CRM record.
            scope = [('res_model', '=', 'crm.lead')]
        records = self.env[model].search_read(expression.AND([scope, filters]), fields,
                                               limit=limit, offset=offset, order='id')
        return {'records': records, 'next_offset': offset + limit if len(records) == limit else None}

    def _crm_values(self, values, creating=False):
        values = crm_values(values, creating)
        # Check relational targets explicitly; writing a many2one alone need not check their read rules.
        for field, model in [('partner_id', 'res.partner'), ('user_id', 'res.users'),
                             ('team_id', 'crm.team'), ('stage_id', 'crm.stage')]:
            if values.get(field):
                related = self._record(model, values[field])
                if 'company_id' in related._fields and related.company_id and related.company_id != self.env.company:
                    raise AccessError('Related record belongs to another company.')
                if field == 'user_id' and (related.share or self.env.company not in related.company_ids):
                    raise AccessError('Assignee must be an internal user of this company.')
        return values

    def _call(self, name, args):
        definitions = {t['name']: t for t in self._tools()}
        if not isinstance(name, str) or name not in definitions:
            raise AccessError('Tool unavailable or not permitted.')
        validate_arguments(definitions[name], args)
        result = self._execute(name, args)
        if not definitions[name]['annotations']['readOnlyHint']:
            # No message bodies, contact details, tokens or CRM descriptions in logs.
            _logger.info('MCP mutation uid=%s company=%s tool=%s record=%s',
                         self.env.uid, self.env.company.id, name, result.get('id'))
        return result

    def _execute(self, name, args):
        if name == 'odoo_catalog':
            return {'resources': {key: {'model': model, 'fields': ['id'] + fields}
                                  for key, (model, fields) in RESOURCES.items()},
                    'tools': [t['name'] for t in self._tools()],
                    'company_id': self.env.company.id,
                    'scope': 'Current company; normal Odoo ACLs and record rules; own To-dos; joined channels.'}
        if name == 'odoo_search':
            return self._search(args)
        if name == 'odoo_messages':
            record = self._target(args)
            domain = [('model', '=', record._name), ('res_id', '=', record.id)]
            if 'before_id' in args:
                domain.append(('id', '<', integer(args['before_id'], 'before_id')))
            return {'records': self.env['mail.message'].search_read(domain,
                    ['id', 'date', 'author_id', 'body', 'message_type', 'subtype_id'],
                    order='id desc', limit=integer(args.get('limit', 50), 'limit', 1, 100))}
        if name == 'crm_create':
            values = self._crm_values(args['values'], True)
            values['company_id'] = self.env.company.id
            return {'id': self.env['crm.lead'].create(values).id}
        if name == 'crm_update':
            record = self._record('crm.lead', args['id'], 'write')
            record.write(self._crm_values(args['values']))
            return {'id': record.id}
        if name == 'crm_action':
            record = self._record('crm.lead', args['id'], 'write')
            action = args['action']
            if action == 'won':
                record.action_set_won()
            elif action == 'lost':
                record.action_set_lost()
            elif action in ('archive', 'restore'):
                record.write({'active': action == 'restore'})
            else:
                raise ValueError('Unsupported CRM action')
            return {'id': record.id}
        if name == 'crm_schedule_activity':
            record = self._record('crm.lead', args['id'], 'write')
            activity_type = self._record('mail.activity.type', args['activity_type_id'])
            if activity_type.category != 'default' or activity_type.res_model not in (False, 'crm.lead'):
                raise ValueError('Only ordinary CRM-compatible activity types are supported')
            user_id = args.get('user_id', self.env.uid)
            self._crm_values({'user_id': user_id})
            deadline = date.fromisoformat(text_value(args['date_deadline'], 'date_deadline', 10))
            activity = self.env['mail.activity'].create({
                'res_model_id': self.env['ir.model']._get_id('crm.lead'), 'res_id': record.id,
                'activity_type_id': activity_type.id, 'user_id': user_id,
                'summary': text_value(args['summary'], 'summary', 256),
                'note': escape(text_value(args.get('note', 'Scheduled via MCP'), 'note')),
                'date_deadline': deadline,
            })
            return {'id': activity.id}
        if name == 'crm_complete_activity':
            activity = self._record('mail.activity', args['id'], 'write')
            if activity.res_model != 'crm.lead':
                raise AccessError('Only CRM activities may be completed.')
            self._record('crm.lead', activity.res_id, 'write')
            activity_id = activity.id
            activity.action_feedback(feedback=escape(text_value(args.get('feedback', 'Completed via MCP'), 'feedback')))
            return {'id': activity_id, 'completed': True}
        if name == 'todo_create':
            values = todo_values(args['values'], True)
            values.update({'project_id': False, 'parent_id': False,
                           'user_ids': [Command.set([self.env.uid])]})
            return {'id': self.env['project.task'].create(values).id}
        if name == 'todo_update':
            record = self._todo(args['id'])
            record.write(todo_values(args['values']))
            return {'id': record.id}
        if name == 'todo_action':
            states = {'complete': '1_done', 'reopen': '01_in_progress', 'cancel': '1_canceled'}
            action = args['action']
            if not isinstance(action, str) or action not in states:
                raise ValueError('Unsupported To-do action')
            record = self._todo(args['id'])
            record.write({'state': states[action]})
            return {'id': record.id, 'state': record.state}
        if name == 'chat_post':
            record = self._target(args)
            message = record.message_post(body=escape(text_value(args['body'], 'body')),
                message_type='comment', subtype_xmlid='mail.mt_comment' if args['target'] == 'channel' else 'mail.mt_note')
            return {'id': message.id}
        raise ValueError('Unknown tool')
